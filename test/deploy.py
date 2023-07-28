#!/usr/bin/python3
import os
import pwd
import json
import yaml
import time
import pylxd
import string
import random
import argparse
import subprocess


# Helper Functions.
def _get_random_string(length: int) -> string:
    """Get a randomised string of given lentgh."""
    return "".join(
        random.choices(string.ascii_uppercase + string.digits, k=length)
    )


# Custom Errors
class PreconditionError(Exception):
    """Custom Error class for unmet precondition errors."""

    def __init__(self, description: string):
        self.description = description

    def __str__(self):
        return repr(self.description)


class Cleaner:
    def __init__(self, model_file_path: string) -> None:
        if os.path.exists(model_file_path):
            # init client
            self.client = pylxd.Client()
            self.clean(model_file_path)
        else:
            print("Model File {} does not exists.".format(model_file_path))

    def clean(self, model_file_path: string) -> None:
        """Clean LXC objects mentioned in the model file."""
        with open(model_file_path, "r") as model_file:
            model = json.loads(model_file.read())

        if "vm_name" in model:
            vm_name = model["vm_name"]
            if self.client.instances.exists(vm_name):
                print("Deleting VM {}".format(vm_name))
                vm = self.client.virtual_machines.get(vm_name)
                vm.stop(wait=True)
                vm.delete(wait=True)

        if "container_name" in model:
            container = model["container_name"]
            if self.client.instances.exists(container):
                print("Deleting VM {}".format(container))
                cm = self.client.containers.get(container)
                cm.stop(wait=True)
                cm.delete(wait=True)

        if "profile" in model:
            profile_name = model["profile"]
            if self.client.profiles.exists(profile_name):
                print("Deleting VM Profile {}".format(profile_name))
                self.client.profiles.get(profile_name).delete()

        if "storage_pool" in model:
            pool_name = model["storage_pool"]
            if self.client.storage_pools.exists(pool_name):
                pool = self.client.storage_pools.get(pool_name)

                if "volumes" in model:
                    volumes = model["volumes"]
                    for volume in volumes:
                        # Delete Volume.
                        print(
                            "Deleting Volume {} from Pool {}".format(
                                volume, pool_name
                            )
                        )
                        pool.volumes.get("custom", volume).delete()

                # Delete Storage pool
                print("Deleting Pool {}".format(pool_name))
                pool.delete()


class DeployRunner:
    # LXD Vars
    model_id = _get_random_string(4)
    deploy_tag = "ubuntu-ceph-" + model_id

    # Repo root dir on remote target (LXD)
    target_repo_path = (
        "/home/ubuntu"  # actual value populated after file sync.
    )

    # Note: following paths are on Host machine, not target machines.
    script_dir = os.path.dirname(os.path.realpath(__file__))  # Script dir.
    root_dir = os.path.dirname(script_dir)  # Repository root dir

    usr = pwd.getpwuid(os.getuid())[0]
    model_file_path = ""
    model = dict()

    def __init__(self, is_direct_host: bool = False) -> None:
        if not is_direct_host:
            # Check LXD installed on host.
            self.check_snaps_installed()
            # init client
            self.client = pylxd.Client()
            # Check if LXD is initialised.
            self.check_lxd_initialised()
        # File to store LXD virtual resource references.
        self.model_file_path = "{}/model-{}.json".format(
            os.getcwd(), self.model_id
        )

    def save_model_json(self):
        """Save lxd resource dictionary to json file."""
        with open(self.model_file_path, "w") as model_file:
            json.dump(self.model, model_file, indent=4)
            print(
                "Model information exported to {}".format(self.model_file_path)
            )

    def check_snaps_installed(self, required_snaps: tuple = None):
        """Check if snap dependencies are met."""
        check_snaps = {"lxd"}
        if required_snaps:
            for snap in required_snaps:
                check_snaps.add(snap)

        cmd = ["snap", "list"]
        output = subprocess.check_output(cmd).decode()
        snaps = list(
            map(
                lambda snap_entry: snap_entry.split(" ")[0],
                output.splitlines(),
            )
        )

        if not all(snap in snaps for snap in check_snaps):
            raise PreconditionError(
                "Required snaps not installed: {}".format(snaps)
            )

    def check_user_in_group(self, group_name="lxd"):
        """Check if current user belongs to provided user group"""
        cmd = [
            "getent",
            "group",
            group_name,
        ]
        output = subprocess.check_output(cmd).decode()

        if not (self.usr in output and group_name in output):
            raise PreconditionError(
                "User {} is not in group {}."
                "\nnewgrp {}"
                "\nsudo usermod -aG {} {}"
                "Output: {}".format(
                    self.usr,
                    group_name,
                    group_name,
                    self.usr,
                    group_name,
                    output,
                )
            )

    def check_lxd_initialised(self) -> None:
        """Check if LXD default profile contains network and root device."""
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        default_devices = ["eth0", "root"]
        devices = self.client.profiles.get("default").devices
        if not all(device in devices for device in default_devices):
            # if both default initialised network and root device do not exist.
            raise PreconditionError(
                "LXD not initialised," "please use 'lxd init --auto'"
            )

    def create_storage_pool(self, driver="dir", pool_name=deploy_tag) -> None:
        """Create storage pool for LXD."""
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        if not self.client.storage_pools.exists(pool_name):
            config = {"name": pool_name, "driver": driver}
            print("Creating Storage Pool {}".format(pool_name))
            self.client.storage_pools.create(config)
        self.model["storage_pool"] = pool_name

    def create_instance_profile(
        self,
        volumes: tuple,
        pool_name=deploy_tag,
        profile_name=deploy_tag,
        is_container=False,
    ) -> None:
        """Create a VM profile for LXD"""
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        if is_container:
            profile_template_name = "container_profile.yaml"
        else:
            profile_template_name = "vm_profile.yaml"

        # If profile exists, it is expected to be already configured.
        if not self.client.profiles.exists(profile_name):
            # Load Profile yaml
            with open(
                self.script_dir + "/" + profile_template_name, "r"
            ) as profile:
                config = yaml.safe_load(profile.read())

            devices = config["devices"]
            # Patch block devices in the profile
            for volume in volumes:
                devices[volume] = {
                    "pool": pool_name,
                    "source": volume,
                    "type": "disk",
                }
            # Create profile.
            print("Creating VM Profile {}".format(profile_name))
            self.client.profiles.create(
                profile_name, config["config"], config["devices"]
            )
            self.model["profile"] = profile_name

    def create_instance(
        self,
        image="ubuntu/jammy",
        flavor="c4-m10",
        pool_name=deploy_tag,
        profile_name=deploy_tag,
        is_start=True,
        is_container=False,
    ) -> string:
        """Create a virtual machine for LXD."""
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        # Create Instance
        instance_name = self.deploy_tag + "-" + _get_random_string(4)
        config = {
            "name": instance_name,
            "storage": pool_name,
            "profiles": [profile_name],
            "devices": {
                "root": {
                    "path": "/",
                    "pool": pool_name,
                    "size": "20GB",
                    "type": "disk",
                }
            },
            "source": {
                "type": "image",
                "certificate": "",
                "alias": image,
                "server": "https://images.linuxcontainers.org",
                "protocol": "simplestreams",
                "mode": "pull",
                "allow_inconsistent": False,
            },
        }

        if not is_container:
            # Add Flavor for VM Instance.
            config["instance_type"] = flavor

        # Create Instance
        if is_container:
            print("Creating Container {}".format(instance_name))
            self.client.containers.create(config, wait=True)
            self.model["container_name"] = instance_name
            if is_start:
                self.client.containers.get(instance_name).start(wait=True)
        else:
            print("Creating VM {}".format(instance_name))
            self.client.virtual_machines.create(config, wait=True)
            self.model["vm_name"] = instance_name
            if is_start:
                self.client.virtual_machines.get(instance_name).start(
                    wait=True
                )
            self.wait_for_instance_ready(instance_name)

        return instance_name  # instance_name for reference.

    def instance_exists(self, instance_name: string) -> bool:
        """Check if LXD VM exists."""
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        if not self.client.instances.exists(instance_name):
            raise PreconditionError(
                "VM {} does not exist.".format(instance_name)
            )

        return True  # It exists.

    def check_call_on_instance(
        self, instance_name: string, cmd: list, is_fail_print=True
    ) -> tuple:
        """Execute Command on Instance."""
        if self.instance_exists(instance_name):
            inner_cmd = ["lxc", "exec", instance_name, "--", *cmd]
            try:
                subprocess.check_call(inner_cmd)
            except subprocess.CalledProcessError as e:
                if is_fail_print:
                    print(
                        "Failed Executing on {}: Output {}".format(
                            instance_name, e
                        )
                    )
                raise e

    def check_output_on_instance_cephadm_shell(
        self, instance_name: string, cmd: list, is_fail_print=True
    ) -> str:
        """Execute cmd on cephadm and return output"""
        if self.instance_exists(instance_name):
            inner_cmd = [
                "lxc",
                "exec",
                instance_name,
                "--",
                "cephadm",
                "shell",
                *cmd,
            ]
            try:
                return subprocess.check_output(inner_cmd).decode("UTF-8")
            except subprocess.CalledProcessError as e:
                if is_fail_print:
                    print(
                        "Failed Cephadm Execution on {}: Output {}".format(
                            instance_name, e
                        )
                    )
                raise e

    def check_output_on_host_cephadm_shell(
        self, cmd: list, is_fail_print=True
    ) -> str:
        """Execute cmd on cephadm and return output"""
        inner_cmd = [
            "sudo",
            "cephadm",
            "shell",
            *cmd,
        ]
        try:
            return subprocess.check_output(inner_cmd).decode("UTF-8")
        except subprocess.CalledProcessError as e:
            if is_fail_print:
                print("Failed Cephadm Execution on Host: Output {}".format(e))
            raise e

    def check_output_on_target_cephadm_shell(
        self, instance_name: string = None, cmd=[]
    ) -> str:
        """Execute cmd on cephadm shell"""
        if instance_name is None:
            return self.check_output_on_host_cephadm_shell(
                cmd=cmd,
            )
        else:
            return self.check_output_on_instance_cephadm_shell(
                instance_name=instance_name,
                cmd=cmd,
            )

    def wait_for_instance_ready(self, instance_name, max_attempt=20) -> None:
        is_container_ready = False
        counter = 0
        while not is_container_ready:
            try:
                self.check_call_on_instance(
                    instance_name, ["ls"], is_fail_print=False
                )
                is_container_ready = True
            except subprocess.CalledProcessError as e:
                counter += 1
                print("Attempt {}: VM not ready".format(counter))
                if counter >= max_attempt:
                    raise e
                time.sleep(10)  # Sleep for 10 sec.

    def push_to_instance_recursively(
        self, instance_name: string, src_path: string, target_path: string
    ) -> None:
        """Send Files (recursively) to VM"""
        if self.instance_exists(instance_name):
            cmd = [
                "lxc",
                "file",
                "push",
                src_path,
                "{}{}".format(instance_name, target_path),
                "-r",
            ]
            print("PUSHING FILES {}".format(cmd))
            try:
                subprocess.check_call(cmd)
            except subprocess.CalledProcessError as e:
                print(
                    "Failed Pushing {} to {}: Output {}".format(
                        src_path, instance_name, e
                    )
                )
                raise e

    def create_storage_volume(
        self,
        size="10GB",
        count=3,
        pool_name=deploy_tag,
    ) -> list:
        """Create Storage Volume from test storage pool"""
        # Note: At the moment of writing this script, using custom block
        # volumes with LXD containers is not supprted.
        # REF: https://github.com/lxc/lxd/issues/10077
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        if not self.client.storage_pools.exists(pool_name):
            raise PreconditionError(
                "Storage Pool {} does not exist.".format(pool_name)
            )

        storage_pool = self.client.storage_pools.get(pool_name)
        volumes = []
        for iter in range(0, count):
            vol_name = "vol-" + _get_random_string(4)
            config = {
                "name": vol_name,
                "type": "custom",
                "content_type": "block",
            }

            # Creating Storage Volume.
            print("Creating Storage Volume {}".format(vol_name))
            storage_pool.volumes.create(config, wait=True)
            # Save volume name for returning
            volumes.append(vol_name)

        self.model["volumes"] = volumes
        return volumes

    def exec_remote_script(
        self,
        instance_name: string,
        relative_script_path: string,
        params=[],
        op_print=True,
    ) -> None:
        """Execute a remote script on LXD VM."""
        if self.instance_exists(instance_name):
            cmd = [
                "bash",
                self.target_repo_path + "/" + relative_script_path,
                *params,
            ]
            if op_print:
                print("Executing on {}: CMD: {}".format(instance_name, cmd))
            self.check_call_on_instance(instance_name, cmd)

    def exec_host_script(
        self,
        relative_script_path: string,
        params=[],
        op_print=True,
    ) -> None:
        """Execute a script directly on host."""
        cmd = [
            "bash",
            self.root_dir + "/" + relative_script_path,
            *params,
        ]
        try:
            if op_print:
                print("Executing on Host: CMD {}".format(cmd))
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as e:
            raise e

    def exec_script_on_target(
        self,
        instance_name: string = None,
        relative_script_path="test/scripts/cephadm_helper.sh",
        params=[],
        op_print=True,
    ) -> None:
        """Execute script on target (Host or LXD machine)"""
        if instance_name is None:
            self.exec_host_script(relative_script_path, params)
        else:
            self.exec_remote_script(
                instance_name, relative_script_path, params
            )

    def install_apt_package(
        self,
        instance_name: string,
        relative_script_path="test/scripts/cephadm_helper.sh",
    ) -> None:
        """Installs the required packages on lxd machine."""
        self.exec_script_on_target(
            instance_name, relative_script_path, ["install_apt"]
        )

    def grow_root_partition(self, instance_name: string) -> None:
        """Use Growpart utility to increase root partition size."""
        self.check_call_on_instance(instance_name,
                                    ["growpart", "/dev/sda", "2"])
        time.sleep(5)  # Sleep for 5 sec.
        self.check_call_on_instance(instance_name, ["resize2fs", "/dev/sda2"])

    def sync_repo_to_instance(
        self,
        instance_name: string,
        src_path: string = None,
        target_path="/home/",
    ) -> None:
        """Copies the Repository to LXD Vm for building"""
        if src_path is None:
            # Going one directory "UP" from test.
            src_path = "/".join(self.script_dir.split("/")[0:-1])

        try:
            # Storing for later use.
            self.target_repo_path = target_path + src_path.split("/")[-1]
        except KeyError as e:
            print(
                "Unable to fetch repo directory from source path {}".format(
                    src_path
                )
            )
            raise e

        # Push repository files to LXD VM.
        self.push_to_instance_recursively(
            instance_name=instance_name,
            src_path=src_path + "/",
            target_path=target_path,
        )

    def prepare_container_image(
        self,
        instance_name: str,
        build_arg: str = None,
        relative_script_path="test/scripts/cephadm_helper.sh",
    ) -> None:
        """Run Helper scripts to make Container image available."""
        # NOTE: The dockerfile is always expected to be at the root of repo.
        if build_arg is not None:
            self.exec_script_on_target(
                instance_name,
                relative_script_path,
                [
                    "prep_docker",
                    "--build-arg",
                    build_arg,
                    self.target_repo_path,
                ],
            )
        else:
            self.exec_script_on_target(
                instance_name,
                relative_script_path,
                ["prep_docker", self.target_repo_path],
            )

    def bootstrap_cephadm(
        self,
        instance_name: string,
        image="localhost:5000/canonical/ceph:latest",
        check_count=10,
    ) -> None:
        """Bootstrap Cephadm."""
        self.exec_script_on_target(
            instance_name=instance_name,
            relative_script_path="test/scripts/cephadm_helper.sh",
            params=["deploy_cephadm", image],
        )

    def add_osds(
        self, instance_name: string, check_count=10, expected_osd_num=3
    ) -> None:
        """Deploy OSD Daemons on all available devices."""
        print("Adding OSDs, it may take a few minutes.")
        status_cmd = ["ceph", "status", "-f", "json"]
        cmd = ["ceph", "orch", "apply", "osd", "--all-available-devices"]
        self.check_output_on_target_cephadm_shell(instance_name, cmd)

        for attempt in range(0, check_count):
            status = json.loads(
                self.check_output_on_target_cephadm_shell(
                    instance_name, status_cmd
                )
            )
            osd_count = status["osdmap"]["num_osds"]
            if osd_count >= expected_osd_num:
                break
            print(
                "Attempt {}: OSD not up! Count {}".format(attempt, osd_count)
            )
            time.sleep(60)  # Wait for a minute

        status = json.loads(
            self.check_output_on_target_cephadm_shell(
                instance_name, status_cmd
            )
        )
        osd_count = status["osdmap"]["num_osds"]
        if osd_count < expected_osd_num:
            raise EnvironmentError("OSDs not up Count {}".format(osd_count))
        print("OSD Count {}".format(osd_count))

    def check_host_cephadm_already_deployed(
        self,
        instance_name: str,
    ) -> None:
        """Check if host already has cephadm based deploymets."""
        inner_cmd = ["sudo", "cephadm", "ls"]
        if instance_name is None:
            result = json.loads(
                subprocess.check_output(inner_cmd).decode("UTF-8")
            )
            if len(result) > 0:
                raise PreconditionError(
                    "A Deployment is already present at host, fsid {}".format(
                        result[0]["fsid"]
                    )
                )

    def configure_insecure_registry(
        self,
        instance_name: str,
        custom_image: str,
    ) -> None:
        """Configure Insecure registry if required."""
        # If it is a self hosted custom image.
        if ":5000" in custom_image:
            registry = custom_image.split(":")[0]
            if registry == "localhost":
                # Docker doesn't need insecure registry entry for localhost.
                return
            self.exec_script_on_target(
                instance_name,
                "test/scripts/cephadm_helper.sh",
                ["configure_insecure_registry", registry],
            )

    def deploy_cephadm(
        self,
        custom_image: str = None,
        build_arg: str = None,
        expected_osd_num: int = 3,
        is_container: bool = False,
        is_direct_host: bool = False,
    ) -> None:
        """Deploy cephadm over LXD host."""
        try:
            if not is_direct_host:
                self.create_storage_pool()

                if not is_container:
                    volumes = self.create_storage_volume()
                else:
                    volumes = []

                self.create_instance_profile(
                    tuple(volumes), is_container=is_container
                )
                instance_name = self.create_instance(is_container=is_container)
                self.sync_repo_to_instance(instance_name)
                self.install_apt_package(instance_name)
            else:
                # None instance name results in direct host operations.
                instance_name = None
                self.install_apt_package(instance_name)
                self.check_host_cephadm_already_deployed(instance_name)

            # Use custom image if provided.
            if custom_image is not None:
                self.configure_insecure_registry(instance_name, custom_image)
                self.bootstrap_cephadm(instance_name, image=custom_image)
            # Build Container Image.
            else:
                self.prepare_container_image(
                    instance_name, build_arg=build_arg
                )
                self.bootstrap_cephadm(instance_name)

            self.add_osds(instance_name, expected_osd_num=expected_osd_num)
            self.save_model_json()
        except Exception as e:
            print("Failed deploying Cephadm over LXD: Error {}".format(e))
            self.save_model_json()  # Save partial info to file for cleanup.
            raise e
        except KeyboardInterrupt:
            print("User Interrupted the deployment process, Exiting.")
            self.save_model_json()


# Subcommand Callbacks
def delete(args):
    print("Executing Clean: {}".format(args))
    Cleaner(args.model_file_path)


def image(args):
    print("Executing Image: {}".format(args))
    runner = DeployRunner(is_direct_host=args.direct_host)
    runner.deploy_cephadm(
        custom_image=args.image_reference,
        expected_osd_num=args.osd_num,
        is_container=args.container,
        is_direct_host=args.direct_host,
    )


def build(args):
    print("Executing Build: {}".format(args))
    # Build with build arguments.
    runner = DeployRunner(is_direct_host=args.direct_host)
    runner.deploy_cephadm(
        build_arg=args.build_args,
        expected_osd_num=args.osd_num,
        is_container=args.container,
        is_direct_host=args.direct_host,
    )


if __name__ == "__main__":
    argparse = argparse.ArgumentParser(
        description="Cephadm Deployment Script",
        epilog="Ex: python3 ./test/deploy.py image canonical/ceph:latest",
    )

    argparse.add_argument(
        "--osd-num",
        type=int,
        default=3,
        help="Optionally provide expected number of osd-daemons.",
    )
    argparse.add_argument(
        "--container",
        type=bool,
        const=True,
        default=False,
        nargs="?",
        help="Perform chosen deployment over lxd container.",
    )
    argparse.add_argument(
        "--direct-host",
        type=bool,
        const=True,
        default=False,
        nargs="?",
        help="Perform chosen deployment directly over host.",
    )

    sub_parsers = argparse.add_subparsers(title="commands", dest="cmd")

    # Delete Subcommand
    del_parser = sub_parsers.add_parser(
        "delete", help="Delete Script generated lxd resources."
    )
    del_parser.add_argument(
        "model_file_path", help="Path to script generated json file."
    )
    del_parser.set_defaults(func=delete)

    # Custom Image  Subcommand
    img_parser = sub_parsers.add_parser(
        "image", help="Use custom images to deploy cephadm."
    )
    img_parser.add_argument(
        "image_reference", help="Fully Qualified image reference"
    )
    img_parser.set_defaults(func=image)

    # Build Subcommand
    build_parser = sub_parsers.add_parser(
        "build", help="Build and deploy cephadm from repo."
    )
    build_parser.add_argument(
        "--build-args", help="Provide optional build-args to Docker."
    )
    build_parser.set_defaults(func=build)

    # Parse the args.
    args = argparse.parse_args()

    # Call the subcommand.
    args.func(args)
