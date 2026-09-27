#!/usr/bin/env python3

from __future__ import annotations

import time
import re
import google.auth
import warnings
import sys
from typing import Any

from google.api_core.extended_operation import ExtendedOperation
from google.cloud import compute_v1

ZONE = "us-west1-b"

#pulled from https://docs.cloud.google.com/compute/docs/samples/compute-operation-extended-wait
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




#adapted from https://docs.cloud.google.com/compute/docs/samples/compute-snapshot-create?hl=en
def create_snapshot(
    project_id: str,
    disk_name: str,
    snapshot_name: str,
    *,
    zone: str | None = None,
    region: str | None = None,
    location: str | None = None,
    disk_project_id: str | None = None,
) -> compute_v1.Snapshot:
    if zone is None and region is None:
        raise RuntimeError(
            "You need to specify `zone` or `region` for this function to work."
        )
    if zone is not None and region is not None:
        raise RuntimeError("You can't set both `zone` and `region` parameters.")

    if disk_project_id is None:
        disk_project_id = project_id

    if zone is not None:
        disk_client = compute_v1.DisksClient()
        disk = disk_client.get(project=disk_project_id, zone=zone, disk=disk_name)
    else:
        regio_disk_client = compute_v1.RegionDisksClient()
        disk = regio_disk_client.get(
            project=disk_project_id, region=region, disk=disk_name
        )

    snapshot = compute_v1.Snapshot()
    snapshot.source_disk = disk.self_link
    snapshot.name = snapshot_name
    if location:
        snapshot.storage_locations = [location]

    snapshot_client = compute_v1.SnapshotsClient()
    operation = snapshot_client.insert(project=project_id, snapshot_resource=snapshot)

    wait_for_extended_operation(operation, "snapshot creation")

    return snapshot_client.get(project=project_id, snapshot=snapshot_name)

#adapted from https://docs.cloud.google.com/compute/docs/samples/compute-instances-create-from-image-plus-snapshot-disk
def disk_from_snapshot(
    disk_type: str,
    disk_size_gb: int,
    boot: bool,
    source_snapshot: str,
    auto_delete: bool = True,
) -> compute_v1.AttachedDisk():
    disk = compute_v1.AttachedDisk()
    initialize_params = compute_v1.AttachedDiskInitializeParams()
    initialize_params.source_snapshot = source_snapshot
    initialize_params.disk_type = disk_type
    initialize_params.disk_size_gb = disk_size_gb
    disk.initialize_params = initialize_params
    # Remember to set auto_delete to True if you want the disk to be deleted when you delete
    # your VM instance.
    disk.auto_delete = auto_delete
    disk.boot = boot
    return disk

#adapted from https://github.com/GoogleCloudPlatform/python-docs-samples/blob/main/compute/client_library/ingredients/instances/create_instance.py
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
    instance_client = compute_v1.InstancesClient()

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
    instance.tags = compute_v1.Tags()
    instance.tags.items = ["allow-5000"]

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

#adapted from https://docs.cloud.google.com/compute/docs/samples/compute-instances-create-from-image-plus-snapshot-disk
def create_with_snapshotted_data_disk(
        project_id: str, zone: str, instance_name: str, snapshot_link: str, snapshot_size: int
):
    disk_type = f"zones/{zone}/diskTypes/pd-standard"
    disks = [
        disk_from_snapshot(disk_type, snapshot_size, True, snapshot_link),
    ]
    instance = create_instance(project_id, zone, instance_name, disks)
    return instance

def main():

    credentials, project_id = google.auth.default()

    snapshot = create_snapshot(project_id,'flask-vm','base-snapshot-flask-vm',zone=ZONE)

    print("Creating first VM")
    start1 = time.time()
    create_with_snapshotted_data_disk(project_id,ZONE,'flask-vm-1',snapshot.self_link,snapshot.disk_size_gb)
    elapsed1 = time.time()-start1
    print(f"VM created from snap. Elapsed time is {elapsed1}")

    print("Creating second VM")
    start2 = time.time()
    create_with_snapshotted_data_disk(project_id,ZONE,'flask-vm-2',snapshot.self_link,snapshot.disk_size_gb)
    elapsed2 = time.time()-start2
    print(f"VM created from snap. Elapsed time is {elapsed2}")

    print("Creating third VM")
    start3 = time.time()
    create_with_snapshotted_data_disk(project_id,ZONE,'flask-vm-3',snapshot.self_link,snapshot.disk_size_gb)
    elapsed3 = time.time()-start3
    print(f"VM created from snap. Elapsed time is {elapsed3}")

    with open('TIMING.md', "w", encoding="utf-8") as f:
        f.write(f"First VM creation time: {elapsed1}\n")
        f.write(f"Second  VM creation time: {elapsed2}\n")
        f.write(f"Third VM creation time: {elapsed3}\n")




if __name__ == "__main__":
    main()
