"""EC2 instance management."""
import sys
from multiprocessing.dummy import Pool

import boto3
import paramiko


class ChainspaceNetwork(object):
    threads = 100
    aws_api_threads = 5

    def __init__(self, network_id, aws_region='us-east-2'):
        self.network_id = str(network_id)

        self.aws_region = aws_region
        self.ec2 = boto3.resource('ec2', region_name=aws_region)

        self.ssh_connections = {}

    def _get_running_instances(self):
        return self.ec2.instances.filter(Filters=[
            {'Name': 'tag:type', 'Values': ['chainspace']},
            {'Name': 'tag:network_id', 'Values': [self.network_id]},
            {'Name': 'instance-state-name', 'Values': ['running']}
        ])

    def _get_stopped_instances(self):
        return self.ec2.instances.filter(Filters=[
            {'Name': 'tag:type', 'Values': ['chainspace']},
            {'Name': 'tag:network_id', 'Values': [self.network_id]},
            {'Name': 'instance-state-name', 'Values': ['stopped']}
        ])

    def _get_all_instances(self):
        return self.ec2.instances.filter(Filters=[
            {'Name': 'tag:type', 'Values': ['chainspace']},
            {'Name': 'tag:network_id', 'Values': [self.network_id]},
        ])

    def _log(self, message):
        _safe_print(message)

    def _log_instance(self, instance, message):
        message = '[instance {}] {}'.format(instance.id, message)
        self._log(message)

    def _single_install_deps(self, instance):
        self._log_instance(instance, "Installing Chainspace dependencies...")
        commands = (
            'sudo apt update',
            'sudo apt install -t jessie-backports openjdk-8-jdk -y',
            'sudo apt install git python-pip maven screen psmisc -y',
        )
        for command in commands:
            self._single_ssh_exec(instance, command)
        self._log_instance(instance, "Installed Chainspace dependencies.")

    def _single_install_core(self, instance):
        self._log_instance(instance, "Installing Chainspace core...")
        commands = (
            'git clone https://github.com/musalbas/chainspace',
            'sudo pip install chainspace/chainspacecontract',
            'sudo update-alternatives --set java /usr/lib/jvm/java-8-openjdk-amd64/jre/bin/java',
            'cd chainspace/chainspacecore; export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64; mvn package assembly:single',
        )
        for command in commands:
            self._single_ssh_exec(instance, command)
        self._log_instance(instance, "Installed Chainspace core.")

    def _single_ssh_connect(self, instance):
        self._log_instance(instance, "Initiating SSH connection...")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=instance.public_ip_address, username='admin')
        self.ssh_connections[instance] = client
        self._log_instance(instance, "Initiated SSH connection.")

    def _single_ssh_exec(self, instance, command):
        self._log_instance(instance, "Executing command: {}".format(command))
        client = self.ssh_connections[instance]
        stdin, stdout, stderr = client.exec_command(command)
        for message in iter(stdout.readline, ''):
            try:
                self._log_instance(instance, message.rstrip())
            except Exception:
                pass
        for message in stderr.readlines():
            try:
                self._log_instance(instance, message.rstrip())
            except Exception:
                pass
        self._log_instance(instance, "Executed command: {}".format(command))

    def _single_ssh_close(self, instance):
        self._log_instance(instance, "Closing SSH connection...")
        client = self.ssh_connections[instance]
        client.close()
        self._log_instance(instance, "Closed SSH connection.")

    def launch(self, count, key_name):
        self._log("Launching {} instances...".format(count))
        self.ec2.create_instances(
            BlockDeviceMappings=[
                {
                    'DeviceName': '/dev/sda1',
                    'Ebs': {
                        'Encrypted': False,
                        'DeleteOnTermination': True,
                        'VolumeSize': 2,
                    },
                },
            ],
            ImageId=_jessie_mapping[self.aws_region], # Debian 8.7
            InstanceType='t2.micro',
            MinCount=count,
            MaxCount=count,
            KeyName=key_name,
            SecurityGroups=['chainspace'],
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [
                        {'Key': 'type', 'Value': 'chainspace'},
                        {'Key': 'network_id', 'Value': self.network_id},
                        {'Key': 'Name', 'Value': 'Chainspace node (network: {})'.format(self.network_id)},
                    ]
                }
            ]
        )
        self._log("Launched {} instances.".format(count))

    def install_deps(self):
        self._log("Installing Chainspace dependencies on all nodes...")
        args = [(self._single_install_deps, instance) for instance in self._get_running_instances()]
        pool = Pool(ChainspaceNetwork.threads)
        pool.map(_multi_args_wrapper, args)
        pool.close()
        pool.join()
        self._log("Installed Chainspace dependencies on all nodes.")

    def install_core(self):
        self._log("Installing Chainspace core on all nodes...")
        args = [(self._single_install_core, instance) for instance in self._get_running_instances()]
        pool = Pool(ChainspaceNetwork.threads)
        pool.map(_multi_args_wrapper, args)
        pool.close()
        pool.join()
        self._log("Installed Chainspace core on all nodes.")

    def ssh_connect(self):
        self._log("Initiating SSH connection on all nodes...")
        args = [(self._single_ssh_connect, instance) for instance in self._get_running_instances()]
        pool = Pool(ChainspaceNetwork.threads)
        pool.map(_multi_args_wrapper, args)
        pool.close()
        pool.join()
        self._log("Initiated SSH connection on all nodes.")

    def ssh_exec(self, command):
        self._log("Executing command on all nodes: {}".format(command))
        args = [(self._single_ssh_exec, instance, command) for instance in self._get_running_instances()]
        pool = Pool(ChainspaceNetwork.threads)
        pool.map(_multi_args_wrapper, args)
        pool.close()
        pool.join()
        self._log("Executed command on all nodes: {}".format(command))

    def ssh_close(self):
        self._log("Closing SSH connection on all nodes...")
        args = [(self._single_ssh_close, instance) for instance in self._get_running_instances()]
        pool = Pool(ChainspaceNetwork.threads)
        pool.map(_multi_args_wrapper, args)
        pool.close()
        pool.join()
        self._log("Closed SSH connection on all nodes...")

    def get_ips(self):
        return [instance.public_ip_address for instance in self._get_running_instances()]

    def terminate(self):
        self._log("Terminating all nodes...")
        self._get_all_instances().terminate()
        self._log("All nodes terminated.")

    def start(self):
        self._log("Starting all nodes...")
        self._get_stopped_instances().start()
        self._log("Started all nodes.")

    def stop(self):
        self._log("Stopping all nodes...")
        self._get_running_instances().stop()
        self._log("Stopped all nodes.")

    def start_core(self):
        self._log("Starting Chainspace core on all nodes...")
        command = 'screen -dmS chainspacecore java -cp chainspace/chainspacecore/target/chainspace-1.0-SNAPSHOT-jar-with-dependencies.jar uk.ac.ucl.cs.sec.chainspace.Main'
        self.ssh_exec(command)
        self._log("Started Chainspace core on all nodes.")

    def stop_core(self):
        self._log("Stopping Chainspace core on all nodes...")
        command = 'killall java' # hacky; should use pid file
        self.ssh_exec(command)
        self._log("Stopping Chainspace core on all nodes.")

    def uninstall_core(self):
        self._log("Uninstalling Chainspace core on all nodes...")
        command = 'rm -rf chainspace; sudo pip uninstall chainspacecontract'
        self.ssh_exec(command)
        self._log("Uninstalled Chainspace core on all nodes.")

    def clean_core(self):
        # TODO: reset state on cores.
        pass

    def config_core(self, shards, nodes_per_shard):
        # TODO: configure cores.
        pass


def _multi_args_wrapper(args):
    return args[0](*args[1:])


def _safe_print(message):
    sys.stdout.write('{}\n'.format(message))


_jessie_mapping = {
    'ap-northeast-1': 'ami-dbc0bcbc',
    'ap-northeast-2': 'ami-6d8b5a03',
    'ap-south-1': 'ami-9a83f5f5',
    'ap-southeast-1': 'ami-0842e96b',
    'ap-southeast-2': 'ami-881317eb',
    'ca-central-1': 'ami-a1fe43c5',
    'eu-central-1': 'ami-5900cc36',
    'eu-west-1': 'ami-402f1a33',
    'eu-west-2': 'ami-87848ee3',
    'sa-east-1': 'ami-b256ccde',
    'us-east-1': 'ami-b14ba7a7',
    'us-east-2': 'ami-b2795cd7',
    'us-west-1': 'ami-94bdeef4',
    'us-west-2': 'ami-221ea342',
}
