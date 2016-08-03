#!/usr/bin/env python
# -*- coding: utf-8 -*-

from datetime import datetime
from mock import MagicMock
from unittest import TestCase
from scripts.solrcloud_backup import BackupController

import json
import os
import re
import shutil
import subprocess
import urllib.request

COMMIT_WAIT_IN_SECONDS = 0
S3_BUCKET = 'test_bucket'
BACKUP_ROOT_DIR = '/data/backup/'
LOCAL_URL = 'http://localhost:8983/solr'

HTTP_CODE_OK = 200

TEST_COLLECTION = 'test_collection'
TEST_SHARD = 'shard1'

TEST_SPLIT_SHARD = 'shard2_1'
TEST_SPLIT_SHARD_NORMALIZED = 'shard4'

TEST_REPLICA_1 = 'replica1'
TEST_REPLICA_2 = 'replica2'


class TestBackupController(TestCase):

    __backup_controller = None
    __subprocess_mock = MagicMock(return_value=0)

    def setUp(self):
        subprocess.call = self.__subprocess_mock
        self.__backup_controller = BackupController(COMMIT_WAIT_IN_SECONDS)
        self.__backup_controller.set_retry_count(1)
        self.__backup_controller.set_retry_wait(0)
        self.__backup_controller.set_restore_retry_count(1)
        self.__backup_controller.set_restore_retry_wait(0)

    def test_should_restore_backup_for_local_shard(self):
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M')
        backup_dir = BACKUP_ROOT_DIR + timestamp
        test_backup_file_name = 'backup_' + timestamp + '_' + TEST_COLLECTION + '_' + TEST_SHARD + '.tar.gz'
        test_core_name = TEST_COLLECTION + '_' + TEST_SHARD + '_' + TEST_REPLICA_1

        local_cores_url = LOCAL_URL + '/admin/cores?action=STATUS&wt=json'
        trigger_restore_url = LOCAL_URL + '/' + test_core_name + '/replication?command=restore&wt=json&location='\
                             + backup_dir + '&name=' + TEST_COLLECTION + '_' + TEST_SHARD
        check_restore_status_url = LOCAL_URL + '/' + test_core_name + '/replication?command=restorestatus&wt=json'

        http_responses = [
            self.__side_effect_local_cores(urllib.request.Request(local_cores_url)),
            self.__side_effect_all_ok(urllib.request.Request(trigger_restore_url)),
            self.__side_effect_restore_in_progress(urllib.request.Request(check_restore_status_url)),
            self.__side_effect_restore_done(urllib.request.Request(check_restore_status_url)),
        ]
        http_mock = MagicMock(side_effect=http_responses)
        urllib.request.urlopen = http_mock

        makedirs_mock = MagicMock(return_value=0)
        os.makedirs = makedirs_mock

        os_remove_mock = MagicMock(return_value=0)
        os.remove = os_remove_mock

        os.listdir = MagicMock(return_value=[timestamp])

        os_path_isfile_mock = MagicMock(return_value=False)
        os.path.isfile = os_path_isfile_mock

        is_dir_responses = [
            False,
            True,
            True,
            True
        ]
        os.path.isdir = MagicMock(side_effect=is_dir_responses)
        shutil_rmtree_mock = MagicMock(return_value=0)
        shutil.rmtree = shutil_rmtree_mock

        self.__backup_controller.restore_backup(bucket=S3_BUCKET, timestamp=timestamp)

        # Verify that backup directory is created
        makedirs_mock.assert_called_once_with(BACKUP_ROOT_DIR + timestamp)

        # Verify that existence of tarball is checked before start of restoring
        os_path_isfile_mock.assert_called_once_with(BACKUP_ROOT_DIR + test_backup_file_name)

        # Verify that backup tarball is downloaded
        s3_file_url = 's3://' + S3_BUCKET + '/' + timestamp + '/' + test_backup_file_name
        self.__subprocess_mock.assert_any_call(['aws', 's3', 'cp', s3_file_url, BACKUP_ROOT_DIR])

        # Verify that backup tarball is unzipped
        self.__subprocess_mock.assert_any_call(
            ['tar', '-xzf', BACKUP_ROOT_DIR + test_backup_file_name, '-C', BACKUP_ROOT_DIR + timestamp]
        )

        # Verify that downloaded file is deleted after unzipping
        os_remove_mock.assert_called_once_with(BACKUP_ROOT_DIR + test_backup_file_name)

        # Verify HTTP requests
        called_urls = list(map(lambda call_args: call_args[0][0].get_full_url(), http_mock.call_args_list))
        expected_urls = [
            local_cores_url,
            trigger_restore_url,
            check_restore_status_url,
            check_restore_status_url
        ]
        self.assertListEqual(called_urls, expected_urls)

        # Verify that backup directory is cleaned up afterwards
        shutil_rmtree_mock.assert_called_once_with(backup_dir)

    def test_should_create_backup_of_local_shards(self):
        self.__test_and_verify_backup_creation(TEST_COLLECTION, TEST_SHARD, self.__side_effect_local_cores)

    def test_should_create_backup_of_local_split_shards(self):
        self.__test_and_verify_backup_creation(TEST_COLLECTION, TEST_SPLIT_SHARD, self.__side_effect_local_split_cores)

    def test_should_create_backup_of_single_core_setup(self):
        self.__test_and_verify_backup_creation(TEST_COLLECTION, '', self.__side_effect_local_single_cores)

    def __test_and_verify_backup_creation(self, collection: str, shard: str, cores_func):
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M')
        backup_dir = BACKUP_ROOT_DIR + timestamp
        normalized_shard = self.__normalize_split_shard(shard)

        if shard != '':
            backup_name = collection + '_' + shard
            normalized_backup_name = collection + '_' + normalized_shard
            core_name = backup_name + '_' + TEST_REPLICA_1
        else:
            backup_name = collection
            normalized_backup_name = collection
            core_name = backup_name

        backup_file_name = 'backup_' + timestamp + '_' + normalized_backup_name + '.tar.gz'
        normalized_snapshot_dir = 'snapshot.' + normalized_backup_name
        snapshot_dir = 'snapshot.' + backup_name

        dir_lists = [
            [],              # backup dir before start of backup
            [snapshot_dir],  # snapshot dirs for zipping and uploading to S3
            [timestamp]      # backup dir for clean up
        ]
        os.listdir = MagicMock(side_effect=dir_lists)

        os.path.isdir = MagicMock(return_value=True)
        shutil_rmtree_mock = MagicMock(return_value=0)
        shutil.rmtree = shutil_rmtree_mock

        os_rename_mock = MagicMock(return_value=0)
        os.rename = os_rename_mock

        local_cores_url = LOCAL_URL + '/admin/cores?action=STATUS&wt=json'
        trigger_commit_url = LOCAL_URL + '/' + collection + '/update?commit=true&wt=json'
        trigger_backup_url = LOCAL_URL + '/' + core_name + '/replication?command=backup&wt=json&location='\
            + backup_dir + '&name=' + backup_name
        check_backup_status_url = LOCAL_URL + '/' + core_name + '/replication?command=details&wt=json'

        http_responses = [
            cores_func(urllib.request.Request(local_cores_url)),
            self.__side_effect_all_ok(urllib.request.Request(trigger_commit_url)),
            cores_func(urllib.request.Request(local_cores_url)),
            self.__side_effect_all_ok(urllib.request.Request(trigger_backup_url)),
            self.__side_effect_backup_in_progress(urllib.request.Request(check_backup_status_url)),
            self.__side_effect_backup_done(urllib.request.Request(check_backup_status_url)),
        ]
        http_mock = MagicMock(side_effect=http_responses)
        urllib.request.urlopen = http_mock

        self.__backup_controller.create_backup(bucket=S3_BUCKET)

        # Verify HTTP requests
        called_urls = list(map(lambda call_args: call_args[0][0].get_full_url(), http_mock.call_args_list))
        expected_urls = [
            local_cores_url,
            trigger_commit_url,
            local_cores_url,
            trigger_backup_url,
            check_backup_status_url,
            check_backup_status_url
        ]
        self.assertListEqual(called_urls, expected_urls)

        # Verify that snapshot directory was renamed
        if shard != normalized_shard:
            split_snapshot_dir_name = backup_dir + '/snapshot.' + backup_name
            normalized_snapshot_dir_name = backup_dir + '/snapshot.' + normalized_backup_name
            os_rename_mock.assert_called_once_with(split_snapshot_dir_name, normalized_snapshot_dir_name)

        # Verify that backup tarball is zipped
        self.__subprocess_mock.assert_any_call(['tar', '-czf', backup_dir + '/' + backup_file_name, '-C',
                                                backup_dir, normalized_snapshot_dir])

        # Verify that backup tarball is uploaded
        s3_url = 's3://' + S3_BUCKET + '/' + timestamp + '/'
        self.__subprocess_mock.assert_any_call(['aws', 's3', 'cp', backup_dir + '/' + backup_file_name, s3_url])

        # Verify that backup directory is cleaned up afterwards
        shutil_rmtree_mock.assert_called_once_with(backup_dir)

    def __normalize_split_shard(self, shard: str):
            if len(shard.split('_')) > 1:
                return TEST_SPLIT_SHARD_NORMALIZED
            else:
                return shard

    def __side_effect_all_ok(self, value):
        self.assertIsNotNone(value)
        response_mock = MagicMock()
        response_mock.getcode.return_value = HTTP_CODE_OK
        return response_mock

    def __side_effect_backup_in_progress(self, value):
        self.assertIsNotNone(value)
        response_mock = MagicMock()
        response_mock.getcode.return_value = HTTP_CODE_OK
        response_mock.read.return_value = \
            bytes(json.dumps({"details": {"backup": ["", "", "", "", "", "In Progress"]}}), 'utf-8')
        return response_mock

    def __side_effect_backup_done(self, value):
        self.assertIsNotNone(value)
        response_mock = MagicMock()
        response_mock.getcode.return_value = HTTP_CODE_OK
        response_mock.read.return_value = \
            bytes(json.dumps({"details": {"backup": ["", "", "", "", "", "success"]}}), 'utf-8')
        return response_mock

    def __side_effect_restore_in_progress(self, value):
        self.assertIsNotNone(value)
        response_mock = MagicMock()
        response_mock.getcode.return_value = HTTP_CODE_OK
        response_mock.read.return_value = \
            bytes(json.dumps({"restorestatus": {"status": "In Progress"}}), 'utf-8')
        return response_mock

    def __side_effect_restore_done(self, value):
        self.assertIsNotNone(value)
        response_mock = MagicMock()
        response_mock.getcode.return_value = HTTP_CODE_OK
        response_mock.read.return_value = \
            bytes(json.dumps({"restorestatus": {"status": "success"}}), 'utf-8')
        return response_mock

    def __side_effect_local_cores(self, value):
        self.assertIsNotNone(value)
        response_mock = MagicMock()
        response_mock.getcode.return_value = HTTP_CODE_OK
        response_mock.read.return_value = \
            bytes(json.dumps({"status": {TEST_COLLECTION + '_' + TEST_SHARD + '_' + TEST_REPLICA_1: {}}}), 'utf-8')
        return response_mock

    def __side_effect_local_split_cores(self, value):
        self.assertIsNotNone(value)
        response_mock = MagicMock()
        response_mock.getcode.return_value = HTTP_CODE_OK
        response_mock.read.return_value = \
            bytes(json.dumps({"status": {TEST_COLLECTION + '_' + TEST_SPLIT_SHARD + '_' + TEST_REPLICA_1: {}}}),
                  'utf-8')
        return response_mock

    def __side_effect_local_single_cores(self, value):
        self.assertIsNotNone(value)
        response_mock = MagicMock()
        response_mock.getcode.return_value = HTTP_CODE_OK
        response_mock.read.return_value = \
            bytes(json.dumps({"status": {TEST_COLLECTION: {}}}),
                  'utf-8')
        return response_mock
