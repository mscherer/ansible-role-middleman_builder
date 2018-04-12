import os

import testinfra.utils.ansible_runner

testinfra_hosts = testinfra.utils.ansible_runner.AnsibleRunner(
    os.environ['MOLECULE_INVENTORY_FILE']).get_hosts('ansible-test-builder')


def test_build(host):
    with host.sudo('web_builder'):
        host.check_output("/usr/local/bin/build_deploy.py -d /srv/builder/testbuilder.yml")

