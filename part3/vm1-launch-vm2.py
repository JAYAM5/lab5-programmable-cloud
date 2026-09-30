from __future__ import annotations

import re
import warnings

from google.cloud import compute_v1
import os
from google.oauth2 import service_account
import sys
from typing import Any

from google.api_core.extended_operation import ExtendedOperation

#
# Use Google Service Account - See https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.service_account.html#module-google.oauth2.service_account
#
credentials = service_account.Credentials.from_service_account_file(filename='/srv/lab5-509822-df0bdf5ff802.json')
project_id = 'lab5-509822'

ZONE = "us-west1-b"
MACHINE_TYPE = "f1-micro"
INSTANCE_NAME = "flask-vm-from-service"
NETWORK_TAG = "allow-5000"
NETWORK = "global/networks/default"
IMAGE = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"

STARTUP = """#!/bin/bash

sudo apt-get update
sudo apt-get install -y python3 python3-pip git

git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial

cd flask-tutorial

sudo python3 setup.py install
sudo pip3 install -e .

export FLASK_APP=flaskr
flask init-db

nohup flask run -h 0.0.0.0 &
"""



#adapted from https://docs.cloud.google.com/compute/docs/samples/compute-operation-extended-wait
def wait_for_extended_operation(
    operation: ExtendedOperation, verbose_name: str = "operation", timeout: int = 300
) -> Any:
    result = operation.result(timeout=timeout)

    if operation.error_code:
        print(
            f"Error during {verbose_name}: [Code: {operation.error_code}]: {operation.error_message}",
            file=sys.stderr,
            flush=True,
        )
        print(f"Operation ID: {operation.name}", file=sys.stderr, flush=True)
        raise operation.exception() or RuntimeError(operation.error_message)

    if operation.warnings:
        print(f"Warnings during {verbose_name}:\n", file=sys.stderr, flush=True)
        for warning in operation.warnings:
            print(f" - {warning.code}: {warning.message}", file=sys.stderr, flush=True)

    return result


#adapted from https://github.com/GoogleCloudPlatform/python-docs-samples/blob/main/compute/client_library/ingredients/instances/create_instance.py
# <INGREDIENT create_instance>
def create_instance(
    project_id: str,
    zone: str,
    instance_name: str,
    disks: list[compute_v1.AttachedDisk],
    machine_type: str = "f1-micro",
    network_link: str = "global/networks/default",
    subnetwork_link: str = None,
    internal_ip: str = None,
    external_access: bool = False,
    external_ipv4: str = None,
    accelerators: list[compute_v1.AcceleratorConfig] = None,
    preemptible: bool = False,
    spot: bool = False,
    instance_termination_action: str = "STOP",
    custom_hostname: str = None,
    delete_protection: bool = False,
) -> compute_v1.Instance:

    instance_client = compute_v1.InstancesClient(credentials=credentials)

    # Use the network interface provided in the network_link argument.
    network_interface = compute_v1.NetworkInterface()
    network_interface.network = network_link
    if subnetwork_link:
        network_interface.subnetwork = subnetwork_link

    if internal_ip:
        network_interface.network_i_p = internal_ip

    if external_access:
        access = compute_v1.AccessConfig()
        access.type_ = compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name
        access.name = "External NAT"
        access.network_tier = access.NetworkTier.PREMIUM.name
        if external_ipv4:
            access.nat_i_p = external_ipv4
        network_interface.access_configs = [access]

    # Collect information into the Instance object.
    instance = compute_v1.Instance()
    instance.network_interfaces = [network_interface]
    instance.name = instance_name
    instance.disks = disks
    if re.match(r"^zones/[a-z\d\-]+/machineTypes/[a-z\d\-]+$", machine_type):
        instance.machine_type = machine_type
    else:
        instance.machine_type = f"zones/{zone}/machineTypes/{machine_type}"

    instance.scheduling = compute_v1.Scheduling()
    if accelerators:
        instance.guest_accelerators = accelerators
        instance.scheduling.on_host_maintenance = (
            compute_v1.Scheduling.OnHostMaintenance.TERMINATE.name
        )

    if preemptible:
        # Set the preemptible setting
        warnings.warn(
            "Preemptible VMs are being replaced by Spot VMs.", DeprecationWarning
        )
        instance.scheduling = compute_v1.Scheduling()
        instance.scheduling.preemptible = True

    if spot:
        # Set the Spot VM setting
        instance.scheduling.provisioning_model = (
            compute_v1.Scheduling.ProvisioningModel.SPOT.name
        )
        instance.scheduling.instance_termination_action = instance_termination_action

    if custom_hostname is not None:
        # Set the custom hostname for the instance
        instance.hostname = custom_hostname

    if delete_protection:
        # Set the delete protection bit
        instance.deletion_protection = True

    # Startup script.
    metadata = compute_v1.Metadata()
    metadata.items = [
        compute_v1.Items(
            key="startup-script",
            value=STARTUP
        )
    ]
    instance.metadata = metadata

    # Prepare the request to insert an instance.
    request = compute_v1.InsertInstanceRequest()
    request.zone = zone
    request.project = project_id
    request.instance_resource = instance

    # Wait for the create operation to complete.
    print(f"Creating the {instance_name} instance in {zone}...")

    operation = instance_client.insert(request=request)

    wait_for_extended_operation(operation, "instance creation")

    print(f"Instance {instance_name} created.")

    return instance_client.get(project=project_id, zone=zone, instance=instance_name)

def create_boot_disk():
    disk = compute_v1.AttachedDisk()
    disk.boot = True
    disk.auto_delete = True

    initialize_params = compute_v1.AttachedDiskInitializeParams()
    initialize_params.source_image = IMAGE
    initialize_params.disk_size_gb = 10

    disk.initialize_params = initialize_params

    return disk


def add_network_tag(project_id):
    instances = compute_v1.InstancesClient(credentials=credentials)

    # Get current tags/fingerprint.
    instance = instances.get(
        project=project_id,
        zone=ZONE,
        instance=INSTANCE_NAME
    )

    tags = compute_v1.Tags()
    tags.items = [NETWORK_TAG]
    tags.fingerprint = instance.tags.fingerprint

    print("Adding allow-5000 tag...")

    request = compute_v1.SetTagsInstanceRequest(
        project=project_id,
        zone=ZONE,
        instance=INSTANCE_NAME,
        tags_resource=tags
    )

    operation = instances.set_tags(request=request)
    operation.result()


def get_external_ip(project_id):
    instances = compute_v1.InstancesClient(credentials=credentials)

    instance = instances.get(
        project=project_id,
        zone=ZONE,
        instance=INSTANCE_NAME
    )

    for interface in instance.network_interfaces:
        for access_config in interface.access_configs:
            if access_config.nat_i_p:
                return access_config.nat_i_p

    return None

def main():

    disk = create_boot_disk()

    create_instance(
        project_id=project_id,
        zone=ZONE,
        instance_name=INSTANCE_NAME,
        disks=[disk],
        machine_type=MACHINE_TYPE,
        network_link=NETWORK,
        external_access=True,
    )

    add_network_tag(project_id)

    ip = get_external_ip(project_id)

    if ip:
        print()
        print("The Flask application is available at:")
        print(f"http://{ip}:5000")
    else:
        print("Could not find external IP address.")


if __name__ == "__main__":
    main()
