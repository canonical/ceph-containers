#!/usr/bin/python3
import os
import sys
import pwd
import json
import yaml
import time
import pylxd
import string
import random
import subprocess


# Helper Functions.
def _get_random_string(length: int) -> string:
    """Get a randomised string of given lentgh."""
    return "".join(
        random.choices(string.ascii_uppercase + string.digits, k=length)
    )


# Custom Errors
class PreconditionError(Exception):
    '''Custom Error class for unmet precondition errors.'''
    def __init__(self, description: string):
        self.description = description

    def __str__(self):
        return (repr(self.description))


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
                vm = self.client.containers.get(vm_name)
                vm.stop(wait=True)
                vm.delete(wait=True)

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
                            "Deleting Volume {} from Pool {}"
                            .format(volume, pool_name)
                        )
                        pool.volumes.get("custom", volume).delete()

                # Delete Storage pool
                print("Deleting Pool {}".format(pool_name))
                pool.delete()


class DeployRunner:
    # LXD Vars
    model_id = _get_random_string(4)
    deploy_tag = "ubuntu-ceph-" + model_id
    complete_repo_path = "/home/ubuntu"
    cwd = os.getcwd()
    # Script Parent Directory.
    spd = os.path.dirname(os.path.realpath(__file__))
    usr = pwd.getpwuid(os.getuid())[0]
    modelFilePath = ""
    model = dict()

    def __init__(self) -> None:
        # Check LXD installed on host.
        self.check_snaps_installed()
        # Check if current user is part of the lxd user group.
        # self.check_user_in_group()
        # init client
        self.client = pylxd.Client()
        # Check if LXD is initialised.
        self.check_lxd_initialised()
        # File to store LXD virtual resource references.
        self.modelFilePath = "{}/model-{}.json".format(
            self.cwd, self.model_id
        )

    def save_model_json(self):
        """Save vm resource dictionary to json file."""
        with open(self.modelFilePath, "w") as model_file:
            json.dump(self.model, model_file, indent=4)
            print("Model information exported to {}"
                  .format(self.modelFilePath))

    def check_snaps_installed(self, required_snaps: tuple = None):
        """Check if snap dependencies are met."""
        check_snaps = {"lxd"}
        if required_snaps:
            for snap in required_snaps:
                check_snaps.add(snap)

        cmd = ["snap", "list"]
        output = subprocess.check_output(cmd).decode()
        snaps = list(map(
                lambda snap_entry: snap_entry.split(" ")[0],
                output.splitlines()
            ))

        if not all(snap in snaps for snap in check_snaps):
            raise PreconditionError("Required snaps not installed: {}"
                                    .format(snaps))

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
                    self.usr, group_name, group_name, self.usr,
                    group_name, output
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
            raise PreconditionError("LXD not initialised,"
                                    "please use 'lxd init --auto'")

    def create_storage_pool(self, driver="dir", pool_name=deploy_tag) -> None:
        """Create storage pool for LXD."""
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        if not self.client.storage_pools.exists(pool_name):
            config = {"name": pool_name, "driver": driver}
            print("Creating Storage Pool {}".format(pool_name))
            self.client.storage_pools.create(config)
        self.model["storage_pool"] = pool_name

    def create_vm_profile(
        self, volumes: tuple, pool_name=deploy_tag, profile_name=deploy_tag
    ) -> None:
        """Create a VM profile for LXD"""
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        # If profile exists, it is expected to be already configured.
        if not self.client.profiles.exists(profile_name):
            # Load Profile yaml
            with open(self.spd + "/profile.yaml", "r") as profile:
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

    def create_vm(
        self,
        image="ubuntu/jammy",
        flavor="c4-m10",
        pool_name=deploy_tag,
        profile_name=deploy_tag,
        is_start=True,
    ) -> string:
        """Create a virtual machine for LXD."""
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        # Create Virtual Machine
        vm_name = self.deploy_tag + "-" + _get_random_string(4)
        config = {
            "name": vm_name,
            # "instance_type": flavor,
            "storage": pool_name,
            "profiles": [profile_name],
            "devices": {
                "root": {
                    "path": "/",
                    "pool": pool_name,
                    "size": "20GB",
                    "type": "disk"
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

        # Create VM
        print("Creating VM {}".format(vm_name))
        self.client.containers.create(config, wait=True)
        self.model["vm_name"] = vm_name

        if is_start:
            self.client.containers.get(vm_name).start(wait=True)

        self.wait_for_vm_ready(vm_name)
        # Increase Root Partition size on VM.
        # self.grow_root_partition(vm_name)
        self.enable_docker_in_lxc(vm_name)
        return vm_name  # vm_name to refer to the newly create vm.

    def vm_exists(self, vm_name: string) -> bool:
        '''Check if LXD VM exists.'''
        if not self.client:
            raise PreconditionError("LXD Client not available to runner.")

        if not self.client.instances.exists(vm_name):
            raise PreconditionError("VM {} does not exist.".format(vm_name))

        return True  # It exists.

    def check_call_on_vm(
        self, vm_name: string, cmd: list, is_fail_print=True
    ) -> tuple:
        """Execute Command on VM."""
        if self.vm_exists(vm_name):
            inner_cmd = ["lxc", "exec", vm_name, "--", *cmd]
            try:
                subprocess.check_call(inner_cmd)
            except subprocess.CalledProcessError as e:
                if is_fail_print:
                    print("Failed Executing on {}: Output {}"
                          .format(vm_name, e))
                raise e

    def check_output_on_cephadm_shell(
        self, vm_name: string, cmd: list, is_fail_print=True
    ) -> str:
        """Execute cmd on cephadm and return output"""
        if self.vm_exists(vm_name):
            inner_cmd = [
                "lxc", "exec", vm_name, "--", "cephadm", "shell", *cmd
            ]
            try:
                return subprocess.check_output(inner_cmd).decode("UTF-8")
            except subprocess.CalledProcessError as e:
                if is_fail_print:
                    print("Failed Cephadm Execution on {}: Output {}"
                          .format(vm_name, e))
                raise e

    def wait_for_vm_ready(self, vm_name, max_attempt=20) -> None:
        isVmReady = False
        counter = 0
        while not isVmReady:
            try:
                self.check_call_on_vm(vm_name, ["ls"], is_fail_print=False)
                isVmReady = True
            except subprocess.CalledProcessError as e:
                counter += 1
                print("Attempt {}: VM not ready".format(counter))
                if counter >= max_attempt:
                    raise e
                time.sleep(10)  # Sleep for 10 sec.

    def push_to_vm_recursively(
        self, vm_name: string, src_path: string, target_path: string
    ) -> None:
        """Send Files (recursively) to VM"""
        if self.vm_exists(vm_name):
            cmd = [
                "lxc",
                "file",
                "push",
                src_path,
                "{}{}".format(vm_name, target_path),
                "-r",
            ]
            print("PUSHING FILES {}".format(cmd))
            try:
                subprocess.check_call(cmd)
            except subprocess.CalledProcessError as e:
                print("Failed Pushing {} to {}: Output {}"
                      .format(src_path, vm_name, e))
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
            raise PreconditionError("Storage Pool {} does not exist."
                                    .format(pool_name))

        storage_pool = self.client.storage_pools.get(pool_name)
        volumes = []
        for iter in range(0, count):
            vol_name = "vol-" + _get_random_string(4)
            config = {
                "name": vol_name, "type": "custom", "content_type": "block"
            }

            # Creating Storage Volume.
            print("Creating Storage Volume {}".format(vol_name))
            storage_pool.volumes.create(config, wait=True)
            # Save volume name for returning
            volumes.append(vol_name)

        self.model["volumes"] = volumes
        return volumes

    def exec_remote_script(
        self, vm_name: string, relative_script_path: string,
        params=[], op_print=True
    ) -> None:
        """Execute a remote script on LXD VM."""
        if self.vm_exists(vm_name):
            cmd = [
                "bash", self.complete_repo_path + "/" + relative_script_path,
                *params
            ]
            if op_print:
                print("Executing on {}: CMD: {}".format(vm_name, cmd))
            self.check_call_on_vm(vm_name, cmd)

    def install_apt_package(
        self,
        vm_name: string,
        relative_script_path="test/scripts/cephadm_helper.sh",
    ) -> None:
        """Installs the required packages on lxd vm."""
        self.exec_remote_script(vm_name, relative_script_path, ["install_apt"])

    def grow_root_partition(self, vm_name: string) -> None:
        """Use Growpart utility to increase root partition size."""
        self.check_call_on_vm(vm_name, ["growpart", "/dev/sda", "2"])
        time.sleep(5)  # Sleep for 5 sec.
        self.check_call_on_vm(vm_name, ["resize2fs", "/dev/sda2"])

    def enable_docker_in_lxc(self, vm_name: string) -> None:
        """Enable Docker inside LXC contianers"""
        if self.vm_exists(vm_name):
            inner_cmd = [
                "lxc", "config", "set", vm_name,
            ]

            try:
                subprocess.check_call([*inner_cmd, "security.nesting=true"])
                subprocess.check_call([
                    *inner_cmd, "security.syscalls.intercept.mknod=true"
                ])
                subprocess.check_call([
                    *inner_cmd, "security.syscalls.intercept.setxattr=true"
                ])
            except subprocess.CalledProcessError as e:
                print("Failed to Enable Docker on {}: Output {}"
                      .format(vm_name, e))
                raise e

    def sync_repo_to_vm(
        self, vm_name: string, src_path: string = None, repo_path="/home/"
    ) -> None:
        """Copies the Repository to LXD Vm for building"""
        if src_path is None:
            # Going one directory "UP" from test.
            src_path = '/'.join(self.spd.split('/')[0:-1])

        try:
            # Storing for later use.
            self.complete_repo_path = repo_path + src_path.split("/")[-1]
        except KeyError as e:
            print("Unable to fetch repo directory from source path {}"
                  .format(src_path))
            raise e

        # Push repository files to LXD VM.
        self.push_to_vm_recursively(
            vm_name=vm_name, src_path=src_path + '/', target_path=repo_path
        )

    def prepare_container_image(
        self,
        vm_name,
        build_arg: str = None,
        tar_file_path: str = None,
        relative_script_path="test/scripts/cephadm_helper.sh",
    ) -> None:
        """Run Helper scripts to make Container image available."""
        # NOTE: The dockerfile is always expected to be at the root of repo.
        # Use provided Tar file to serve container image.
        if tar_file_path is not None:
            self.exec_remote_script(
                vm_name, relative_script_path,
                [
                    "prep_docker_for_tar",
                    self.complete_repo_path+"/"+tar_file_path.split('/')[-1]
                ]
            )
        # Use provided arg for building container image.
        elif build_arg is not None:
            self.exec_remote_script(
                vm_name, relative_script_path,
                [
                    "prep_docker", "--build-arg", build_arg,
                    self.complete_repo_path
                ]
            )
        else:
            self.exec_remote_script(
                vm_name, relative_script_path,
                ["prep_docker", self.complete_repo_path]
            )

    def bootstrap_cephadm(
        self,
        vm_name: string,
        image="localhost:5000/canonical/ceph:latest",
        check_count=10,
    ) -> None:
        """Bootstrap Cephadm using cephadm-test script."""
        self.exec_remote_script(
            vm_name, "scripts/cephadm-test.sh", ["deploy_cephadm", image]
        )

        status_cmd = ["ceph", "status", "-f", "json"]
        for attempt in range(0, check_count):
            status = json.loads(
                self.check_output_on_cephadm_shell(vm_name, status_cmd)
            )
            mon_count = status["monmap"]["num_mons"]
            is_mgr_available = status["mgrmap"]["available"]
            if is_mgr_available and mon_count > 0:
                break
            print(
                "Attempt {}: ceph cluster not up, mon_count {}".format(
                    attempt, mon_count
                )
            )
            time.sleep(30)  # Wait for 30 sec.

    def add_osds(
        self, vm_name: string, check_count=10, expected_osd_num=3
    ) -> None:
        """Deploy OSD Daemon using cephadm-test script."""
        print("Adding OSDs, it may take a few minutes.")
        status_cmd = ["ceph", "status", "-f", "json"]
        cmd = ["ceph", "orch", "apply", "osd", "--all-available-devices"]
        self.check_output_on_cephadm_shell(vm_name, cmd)

        for attempt in range(0, check_count):
            status = json.loads(
                self.check_output_on_cephadm_shell(vm_name, status_cmd)
            )
            osd_count = status["osdmap"]["num_osds"]
            if osd_count >= expected_osd_num:
                break
            print("Attempt {}: OSD not up! Count {}"
                  .format(attempt, osd_count))
            time.sleep(60)  # Wait for a minute

        status = json.loads(
            self.check_output_on_cephadm_shell(vm_name, status_cmd)
        )
        osd_count = status["osdmap"]["num_osds"]
        if osd_count < expected_osd_num:
            raise EnvironmentError("OSDs not up Count {}".format(osd_count))
        print("OSD Count {}".format(osd_count))

    def patch_ceph_rules_for_single_node(self, vm_name: string) -> None:
        """Creates a suitable replication rule for single node deployments."""
        # Create a new rule with OSD as failure domain.
        new_rule_name = "new_replication_rule_osd"
        cmd = [
            "ceph",
            "osd",
            "crush",
            "rule",
            "create-replicated",
            new_rule_name,
            "default",
            "osd",
        ]
        self.check_output_on_cephadm_shell(vm_name, cmd)

        # Make new rule default for all pools.
        cmd = ["ceph", "osd", "pool", "ls", "-f", "json"]
        pools = json.loads(self.check_output_on_cephadm_shell(vm_name, cmd))
        for pool in pools:
            cmd = [
                "ceph", "osd", "pool", "set", pool,
                "crush_rule", new_rule_name
            ]
            self.check_output_on_cephadm_shell(vm_name, cmd)

        # Fetch and delete other replication rules
        cmd = ["ceph", "osd", "crush", "rule", "dump", "-f", "json"]
        rules = json.loads(self.check_output_on_cephadm_shell(vm_name, cmd))
        for rule in list(rules):
            if rule["rule_name"] == new_rule_name:
                continue
            cmd = [
                "ceph",
                "osd",
                "crush",
                "rule",
                "rm",
                rule["rule_name"],
            ]
            self.check_output_on_cephadm_shell(vm_name, cmd)

    def deploy_cephadm(
        self, custom_image: str = None, build_arg: str = None,
        tar_file_path: str = None
    ) -> None:
        '''Deploy cephadm over LXD host.'''
        try:
            self.create_storage_pool()
            # volumes = self.create_storage_volume()
            volumes = []
            self.create_vm_profile(tuple(volumes))
            vm_name = self.create_vm()
            self.sync_repo_to_vm(vm_name)
            self.install_apt_package(vm_name)

            # Use custom image if provided.
            if custom_image is not None:
                # Configure Insecure registry if required.
                if ':5000' in custom_image:
                    registry = custom_image.split(':')[0]
                    self.exec_remote_script(
                        vm_name, "test/scripts/cephadm_helper.sh",
                        ["configure_insecure_registry", registry]
                    )
                self.bootstrap_cephadm(vm_name, image=custom_image)
            # Use built tar file to serve image.
            elif tar_file_path is not None:
                # Push repository files to LXD VM.
                self.push_to_vm_recursively(
                    vm_name=vm_name, src_path=tar_file_path,
                    target_path=self.complete_repo_path
                )
                self.prepare_container_image(
                    vm_name, tar_file_path=tar_file_path
                )
                self.bootstrap_cephadm(vm_name)
            # Build Container Image.
            else:
                self.prepare_container_image(vm_name, build_arg=build_arg)
                self.bootstrap_cephadm(vm_name)

            self.add_osds(vm_name)
            self.patch_ceph_rules_for_single_node(vm_name)
            self.save_model_json()
        except Exception as e:
            print("Failed deploying Cephadm over LXD: Error {}".format(e))
            self.save_model_json()  # Save partial info to file for cleanup.
            raise e


def print_script_help() -> None:
    print("Script Usage Help:")
    print("1.) Use Script to deploy a new Cephadm host:\n$ python3 deploy.py")
    print("2.) Use Script to clean a deployment:\n$ python3 deploy.py delete "
          "<model_file_path>")
    print("3.) Use Script to deploy a custom image:\n$ python3 deploy.py "
          "image <image_name>\n-> e.g. <image_name>: ceph/ceph:latest")
    print("Incorrect Usage will print Help Text.")


if __name__ == "__main__":
    vm_name = ""
    volumes = []
    # total arguments
    num_args = len(sys.argv)
    try:
        # Deploy Cephadm over LXD if no arguments passed.
        if num_args == 1:
            runner = DeployRunner()
            runner.deploy_cephadm()
        else:  # If arguments are passed.
            if sys.argv[1] == "delete":
                Cleaner(sys.argv[2])
            # Deploy cephadm with custom image from registry.
            elif sys.argv[1] == "image":
                image = sys.argv[2]
                runner = DeployRunner()
                runner.deploy_cephadm(custom_image=image)
            # If additional build args are passed for docker build.
            elif sys.argv[1] == "build-arg":
                arg = sys.argv[2]
                runner = DeployRunner()
                runner.deploy_cephadm(build_arg=arg)
            # If ceph-container image is passed as a tar.
            elif sys.argv[1] == "tar":
                arg = sys.argv[2]
                runner = DeployRunner()
                runner.deploy_cephadm(tar_file_path=arg)
            else:
                print_script_help()
    except Exception as e:
        print("Operation Failed: Error {}".format(e))
        raise e
