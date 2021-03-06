import pytest
import docker
import yaml

from .base import BaseAPIIntegrationTest
from ..helpers import requires_api_version

import utils
import urllib3
from etcdutil import EtcdUtil
from hpe3parclient import exceptions as exc
from hpe3parclient.client import HPE3ParClient


# Importing test data from YAML config file
with open("testdata/test_config.yml", 'r') as ymlfile:
    cfg = yaml.load(ymlfile)

# Declaring Global variables and assigning the values from YAML config file
HPE3PAR = cfg['plugin']['latest_version']
ETCD_HOST = cfg['etcd']['host']
ETCD_PORT = cfg['etcd']['port']
CLIENT_CERT = cfg['etcd']['client_cert']
CLIENT_KEY = cfg['etcd']['client_key']
HPE3PAR_API_URL = cfg['backend']['3Par_api_url']

@requires_api_version('1.21')
class HPE3ParVolumePluginTest(BaseAPIIntegrationTest):
    """
    This class covers all base methods to verify entities in Docker Engine.
    """
    def hpe_create_volume(self, name, driver, **kwargs):
        if 'flash_cache' in kwargs:
            kwargs['flash-cache'] = kwargs.pop('flash_cache')
        # Create a volume
        docker_volume = self.client.create_volume(name=name, driver=driver,
                                                  driver_opts=kwargs)
        # Verify volume entry in docker managed plugin system
        self.assertIn('Name', docker_volume)
        self.assertEqual(docker_volume['Name'], name)
        self.assertIn('Driver', docker_volume)
        self.assertEqual(docker_volume['Driver'], HPE3PAR)
        # Verify all volume optional parameters in docker managed plugin system
        if 'size' in kwargs:
            self.assertIn('size', docker_volume['Options'])
            self.assertEqual(docker_volume['Options']['size'], kwargs['size'])
        else:
            self.assertNotIn('size', docker_volume['Options'])
        if 'provisioning' in kwargs:
            self.assertIn('provisioning', docker_volume['Options'])
            self.assertEqual(docker_volume['Options']['provisioning'], kwargs['provisioning'])
        else:
            self.assertNotIn('provisioning', docker_volume['Options'])
        if 'flash-cache' in kwargs:
            self.assertIn('flash-cache', docker_volume['Options'])
            self.assertEqual(docker_volume['Options']['flash-cache'], kwargs['flash-cache'])
        else:
            self.assertNotIn('flash-cache', docker_volume['Options'])
        if 'compression' in kwargs:
            self.assertIn('compression', docker_volume['Options'])
            self.assertEqual(docker_volume['Options']['compression'], kwargs['compression'])
        else:
            self.assertNotIn('compression', docker_volume['Options'])
        return docker_volume

    def hpe_delete_volume(self, name):
        # Delete a volume
        self.client.remove_volume(name)
        result = self.client.volumes()
        volumes = result['Volumes']
        # Verify if volume is deleted from docker managed plugin system
        self.assertEqual(volumes, None)

    def hpe_inspect_volume(self, name, driver, **kwargs):
        # Create a volume
        docker_volume = self.client.create_volume(name=name, driver=driver,
                                                  driver_opts=kwargs)
        # Inspect a volume.
        inspect_volume = self.client.inspect_volume(name)
        self.assertEqual(docker_volume, inspect_volume)
        return inspect_volume

    def hpe_create_host_config(self, volume_driver, binds, *args, **kwargs):
        # Create a host configuration to setup container
        host_config = self.client.create_host_config(volume_driver=volume_driver,
                                                     binds=[binds], *args, **kwargs)
        return host_config

    def hpe_create_container(self, image, command, host_config, *args, **kwargs):
        # Create a container
        container_info = self.client.create_container(image, command=command,
                                                      host_config=host_config,
                                                      *args, **kwargs
                                                      )
        self.assertIn('Id', container_info)
        id = container_info['Id']
        self.tmp_containers.append(id)
        return container_info

    def hpe_mount_volume(self, image, command, host_config, *args, **kwargs):
        # Create a container
        container_info = self.client.create_container(image, command=command,
                                                      host_config=host_config,
                                                      *args, **kwargs
                                                      )
        self.assertIn('Id', container_info)
        id = container_info['Id']
        self.tmp_containers.append(id)
        # Mount volume to this container
        self.client.start(id)
        # Inspect this container
        inspect_start = self.client.inspect_container(id)
        # Verify if container is mounted correctly in docker host.
        self.assertIn('Config', inspect_start)
        self.assertIn('Id', inspect_start)
        self.assertTrue(inspect_start['Id'].startswith(id))
        self.assertIn('Image', inspect_start)
        self.assertIn('State', inspect_start)
        self.assertIn('Running', inspect_start['State'])
        self.assertEqual(inspect_start['State']['Running'], True)
        self.assertNotEqual(inspect_start['Mounts'], None)
        mount = dict(inspect_start['Mounts'][0])
        self.assertEqual(mount['Driver'], HPE3PAR)
        self.assertEqual(mount['RW'], True)
        self.assertEqual(mount['Type'], 'volume')
        self.assertNotEqual(mount['Source'], None)
        if not inspect_start['State']['Running']:
            self.assertIn('ExitCode', inspect_start['State'])
            self.assertEqual(inspect_start['State']['ExitCode'], 0)

    def hpe_unmount_volume(self, image, command, host_config, *args, **kwargs):
        # Create a container
        container_info = self.client.create_container(image, command=command,
                                                      host_config=host_config,
                                                      *args, **kwargs
                                                      )
        self.assertIn('Id', container_info)
        id = container_info['Id']
        self.tmp_containers.append(id)
        # Mount volume to this container
        self.client.start(id)
        # Unmount volume
        self.client.stop(id)
        # Inspect this container
        inspect_stop = self.client.inspect_container(id)
        self.assertIn('State', inspect_stop)
        # Verify if container is unmounted correctly in docker host.
        state = inspect_stop['State']
        self.assertIn('Running', state)
        self.assertEqual(state['Running'], False)

class HPE3ParBackendVerification(BaseAPIIntegrationTest):
    """
    This class covers all the methods to verify entities in 3Par array.
    """

    def _hpe_get_3par_client_login(self):
        # Login to 3Par array and initialize connection for WSAPI calls
        hpe_3par_cli = HPE3ParClient(HPE3PAR_API_URL)
        hpe_3par_cli.login('3paradm', '3pardata')
        return hpe_3par_cli

    def hpe_verify_volume_created(self, volume_name, **kwargs):

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        hpe3par_cli = self._hpe_get_3par_client_login()

        # Get volume details from etcd service
        et = EtcdUtil(ETCD_HOST, ETCD_PORT, CLIENT_CERT, CLIENT_KEY)
        etcd_volume = et.get_vol_byname(volume_name)

        etcd_volume_id = etcd_volume['id']
        backend_volume_name = utils.get_3par_vol_name(etcd_volume_id)
        # Get volume details from 3Par array
        hpe3par_volume = hpe3par_cli.getVolume(backend_volume_name)
        # Verify volume and its properties in 3Par array
        self.assertEqual(hpe3par_volume['name'], backend_volume_name)
        if 'size' in kwargs:
            self.assertEqual(hpe3par_volume['sizeMiB'], int(kwargs['size']) * 1024)
        else:
            self.assertEqual(hpe3par_volume['sizeMiB'], 102400)
        if 'provisioning' in kwargs:
            if kwargs['provisioning'] == 'full':
                self.assertEqual(hpe3par_volume['provisioningType'], 1)
            elif kwargs['provisioning'] == 'thin':
                self.assertEqual(hpe3par_volume['provisioningType'], 2)
            elif kwargs['provisioning'] == 'dedup':
                self.assertEqual(hpe3par_volume['provisioningType'], 6)
            else:
                self.assertEqual(hpe3par_volume['provisioningType'], 2)
        if 'flash_cache' in kwargs:
            if kwargs['flash_cache'] == 'true':
                vvset_name = utils.get_3par_vvset_name(etcd_volume_id)
                vvset = hpe3par_cli.getVolumeSet(vvset_name)
                # Ensure flash-cache-policy is set on the vvset
                self.assertEqual(vvset['flashCachePolicy'], 1)
                # Ensure the created volume is a member of the vvset
                self.assertIn(backend_volume_name,
                              [vv_name for vv_name in vvset['setmembers']]
                              )
            else:
                vvset_name = utils.get_3par_vvset_name(etcd_volume_id)
                vvset = hpe3par_cli.getVolumeSet(vvset_name)
                # Ensure vvset is not available in 3par.
                self.assertEqual(vvset, None)
        if 'compression' in kwargs:
            if kwargs['compression'] == 'true':
                self.assertEqual(hpe3par_volume['compressionState'], 1)
            else:
                self.assertEqual(hpe3par_volume['compressionState'], 2)
        hpe3par_cli.logout()

    def hpe_verify_volume_deleted(self, volume_name):

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        hpe3par_cli = self._hpe_get_3par_client_login()

        # Get volume details from etcd service
        et = EtcdUtil(ETCD_HOST, ETCD_PORT, CLIENT_CERT, CLIENT_KEY)
        etcd_volume = et.get_vol_byname(volume_name)
        if etcd_volume is not None:
            etcd_volume_id = etcd_volume['id']
            backend_volume_name = utils.get_3par_vol_name(etcd_volume_id)
            # Get volume details from 3Par array
            hpe3par_volume = hpe3par_cli.getVolume(backend_volume_name)
            self.assertEqual(hpe3par_volume['name'], None)
        else:
            # Verify volume is removed from 3Par array
            self.assertEqual(etcd_volume, None)
        hpe3par_cli.logout()

    def hpe_verify_volume_mount(self, volume_name):

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        hpe3par_cli = self._hpe_get_3par_client_login()
        # Get volume details from etcd service
        et = EtcdUtil(ETCD_HOST, ETCD_PORT, CLIENT_CERT, CLIENT_KEY)
        etcd_volume = et.get_vol_byname(volume_name)
        etcd_volume_id = etcd_volume['id']
        # Get volume details and VLUN details from 3Par array
        backend_volume_name = utils.get_3par_vol_name(etcd_volume_id)
        vlun = hpe3par_cli.getVLUN(backend_volume_name)
        # Verify VLUN is present in 3Par array.
        self.assertNotEqual(vlun, None)
        hpe3par_cli.logout()

    def hpe_verify_volume_unmount(self, volume_name):

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        hpe3par_cli = self._hpe_get_3par_client_login()
        # Get volume details from etcd service
        et = EtcdUtil(ETCD_HOST, ETCD_PORT, CLIENT_CERT, CLIENT_KEY)
        etcd_volume = et.get_vol_byname(volume_name)
        etcd_volume_id = etcd_volume['id']
        # Get volume details and VLUN details from 3Par array
        backend_volume_name = utils.get_3par_vol_name(etcd_volume_id)

        try:
            vlun = hpe3par_cli.getVLUN(backend_volume_name)
            # Verify VLUN is not present in 3Par array.
            self.assertEqual(vlun, None)
        except exc.HTTPNotFound:
            return
        hpe3par_cli.logout()
