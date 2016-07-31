#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import os
import pytz
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from argparse import ArgumentParser
from apscheduler.schedulers.blocking import BlockingScheduler
from datetime import datetime
from threading import Thread

LOCAL_URL = 'http://localhost:8983/solr'
BACKUP_ROOT_DIR = '/data/backup/'
DATA_DIR = '/data/'

DEFAULT_COMMIT_WAIT_IN_SECONDS = 120

DEFAULT_RETRY_COUNT = 30
DEFAULT_RETRY_WAIT_IN_SECONDS = 5

DEFAULT_RESTORE_RETRY_COUNT = 30
DEFAULT_RESTORE_RETRY_WAIT_IN_SECONDS = 60

TIMESTAMP_MINUTE = 0
TIMESTAMP_HOUR = 1
TIMESTAMP_DOW_SCHEDULER = 'sun'
TIMESTAMP_DOW_CRON = 0

REGEX_SHARDED_CORES = "([a-z_]+)_(shard[0-9_]+)_(replica[0-9]+)"
REGEX_SINGLE_CORE = "([a-z_]+)"

LOGGING_LEVEL = logging.INFO


class BackupController:

    __cluster_state = None
    __wait_timeout = 0
    __retry_count = DEFAULT_RETRY_COUNT
    __retry_wait = DEFAULT_RETRY_WAIT_IN_SECONDS
    __restore_retry_count = DEFAULT_RESTORE_RETRY_COUNT
    __restore_retry_wait = DEFAULT_RESTORE_RETRY_WAIT_IN_SECONDS

    def __init__(self, wait_timeout: int):
        self.__wait_timeout = wait_timeout

    def create_backup(self, bucket: str, cleanup=True):
        try:
            if len(os.listdir(BACKUP_ROOT_DIR)) > 0:
                raise Exception('Backup root directory is not empty.')

            timestamp = datetime.utcnow().strftime('%Y%m%d%H%M')
            self.__trigger_local_commit()
            self.__backup_local_shards(timestamp=timestamp)
            self.__store_local_backup_on_s3(bucket=bucket, timestamp=timestamp)
        except Exception as e:
            logging.error('ERROR Backup failed: {}'.format(e))
        finally:
            if cleanup:
                self.__clean_up_backup_dir()

    def restore_backup(self, bucket: str, timestamp: str, cleanup=True):
        self.__restore_latest_backup(bucket=bucket, timestamp=timestamp)
        if cleanup:
            self.__clean_up_backup_dir()

    def set_retry_count(self, retry_count):
        self.__retry_count = retry_count

    def set_retry_wait(self, retry_wait):
        self.__retry_wait = retry_wait

    def set_restore_retry_count(self, retry_count):
        self.__restore_retry_count = retry_count

    def set_restore_retry_wait(self, retry_wait):
        self.__restore_retry_wait = retry_wait

    def set_store_backup_wait(self, wait):
        self.__store_backup_wait = wait

    def __backup_local_shards(self, timestamp: str):
        logging.info('Start creating local backup for timestamp [{}].'.format(timestamp))
        for core_name in self.__get_local_cores():
            sharded_regex_match = re.match(REGEX_SHARDED_CORES, core_name)
            single_regex_match = re.match(REGEX_SINGLE_CORE, core_name)
            if sharded_regex_match:
                collection_name = sharded_regex_match.group(1)
                shard_name = sharded_regex_match.group(2)
                replica_name = sharded_regex_match.group(3)
                full_shard_name = collection_name + '_' + shard_name
                core_name = full_shard_name + '_' + replica_name
            elif single_regex_match:
                collection_name = single_regex_match.group(1)
                full_shard_name = collection_name
                core_name = full_shard_name
            else:
                raise Exception('Unknown core name format [{}]'.format(core_name))

            url = LOCAL_URL + '/' + core_name + '/replication?command=backup&wt=json'
            url += '&location=' + BACKUP_ROOT_DIR + timestamp
            url += '&name=' + full_shard_name
            logging.info('Creating backup for [{}] ...'.format(full_shard_name))
            self.__send_http_request(url)

            # Wait until backup is complete
            check_url = LOCAL_URL + '/' + core_name + '/replication?command=details&wt=json'
            status = 'In Progress'
            retry = 0
            while status == 'In Progress' and retry < self.__retry_count:
                response = json.loads(self.__send_http_request(check_url))
                logging.debug('Status response: [{}]'.format(response))
                if 'details' in response and 'backup' in response['details']\
                        and len(response['details']['backup']) > 5:
                    status = response['details']['backup'][5]
                else:
                    logging.info('Backup status could not be derived from response ... retrying')
                    retry += 1
                time.sleep(self.__retry_wait)

            if status == 'success':
                logging.info('Backup for [{}] successful'.format(full_shard_name))
            else:
                raise Exception('Error while creating backup for [{}]'.format(full_shard_name))
        logging.info('Successfully created local backup for timestamp [{}].'.format(timestamp))

    def __store_local_backup_on_s3(self, bucket: str, timestamp: str):
        regex_shard_backup_dir = "snapshot\.([a-z_]+)_shard([0-9_]+)"
        regex_single_core_backup_dir = "snapshot\.([a-z_]+)"
        backup_dir = BACKUP_ROOT_DIR + timestamp
        logging.info('Start zipping and uploading of backup to S3.')
        threads = []
        for entry in os.listdir(backup_dir):
            if entry.startswith('snapshot.'):
                regex_shard_backup_dir_match = re.match(regex_shard_backup_dir, entry)
                regex_single_core_backup_dir_match = re.match(regex_single_core_backup_dir, entry)
                if regex_shard_backup_dir_match:
                    collection_name = regex_shard_backup_dir_match.group(1)
                    shard_number = regex_shard_backup_dir_match.group(2)
                elif regex_single_core_backup_dir_match:
                    collection_name = regex_single_core_backup_dir_match.group(1)
                    shard_number = ''
                else:
                    raise Exception('Unknown core name format [{}]'.format(entry))

                thread = Thread(target=self.__store_single_backup_on_s3_task,
                                args=(bucket, timestamp, collection_name, shard_number))
                thread.start()
                threads.append(thread)

        # Check that all threads have been finished before finishing
        for thread in threads:
            thread.join()
        logging.info('Finished zipping and uploading of backup to S3.')

    def __store_single_backup_on_s3_task(self, bucket: str, timestamp: str, collection_name: str, shard_number: str):
        backup_dir = BACKUP_ROOT_DIR + timestamp
        shard_number_parts = shard_number.split('_')
        logging.info("Create archive for collection [{}], shard number [{}]".format(collection_name, shard_number))

        if shard_number != '':
            core_backup_dir_name = 'snapshot.' + collection_name + '_shard' + shard_number
        else:
            core_backup_dir_name = 'snapshot.' + collection_name

        # Normalize shard and backup directory name if it is split (only one-time splits are supported)
        if len(shard_number_parts) > 1:
            first_shard_number_part = shard_number_parts[0]
            second_shard_number_part = shard_number_parts[1]
            new_shard_number = str(2 * int(first_shard_number_part) + int(second_shard_number_part) - 1)
            new_shard_backup_dir_name = 'snapshot.' + collection_name + '_shard' + new_shard_number
            os.rename(backup_dir + '/' + core_backup_dir_name, backup_dir + '/' + new_shard_backup_dir_name)
            shard_number = new_shard_number
            core_backup_dir_name = new_shard_backup_dir_name

        if shard_number != '':
            backup_file_name = 'backup_' + timestamp + '_' + collection_name + '_shard' + shard_number + '.tar.gz'
        else:
            backup_file_name = 'backup_' + timestamp + '_' + collection_name + '.tar.gz'

        full_backup_file_name = backup_dir + '/' + backup_file_name

        zip_result = -1
        retry = 0
        while zip_result != 0 and retry < self.__retry_count:
            zip_result = self.__zip_backup_file(full_backup_file_name, backup_dir, core_backup_dir_name)
            if zip_result != 0:
                logging.warning('Creating tarball [{}] failed with result code [{}] ... retrying'
                                .format(full_backup_file_name, zip_result))
                retry += 1
                time.sleep(self.__retry_wait)
        if zip_result != 0:
            raise Exception('Creating tarball [{}] failed with result code [{}]'
                            .format(full_backup_file_name, zip_result))

        upload_result = self.__upload_file_to_s3(bucket=bucket, prefix=timestamp, file_name=full_backup_file_name)
        if upload_result != 0:
            raise Exception('Uploading [{}] to S3  failed with result code [{}]'
                            .format(full_backup_file_name, zip_result))

        logging.info("Successfully created archive for collection [{}], shard number [{}]"
                     .format(collection_name, shard_number))

    def __restore_core(self, core_name: str, timestamp: str):
        sharded_regex_match = re.match(REGEX_SHARDED_CORES, core_name)
        single_regex_match = re.match(REGEX_SINGLE_CORE, core_name)
        if sharded_regex_match:
            collection_name = sharded_regex_match.group(1)
            shard_name = sharded_regex_match.group(2)
            replica_name = sharded_regex_match.group(3)
            full_shard_name = collection_name + '_' + shard_name
            core_name = full_shard_name + '_' + replica_name
        elif single_regex_match:
            collection_name = single_regex_match.group(1)
            full_shard_name = collection_name
            core_name = full_shard_name
        else:
            raise Exception('Unknown core name format [{}]'.format(core_name))

        url = LOCAL_URL + '/' + core_name + '/replication?command=restore&wt=json'
        url += '&location=' + BACKUP_ROOT_DIR + timestamp
        url += '&name=' + full_shard_name
        logging.info('Restoring backup for [{}] locally ...'.format(full_shard_name))
        self.__send_http_request(url)

        # Wait until backup restoration is complete
        check_url = LOCAL_URL + '/' + core_name + '/replication?command=restorestatus'
        check_url += '&wt=json'
        status = 'In Progress'
        retry = 0
        response = None
        while status == 'In Progress' and retry < self.__retry_count:
            response = json.loads(self.__send_http_request(check_url))
            logging.debug('Status response: [{}]'.format(response))
            if 'restorestatus' in response and 'status' in response['restorestatus']:
                status = response['restorestatus']['status']
            else:
                logging.info('Backup status could not be derived from response ... retrying')
                retry += 1
            time.sleep(self.__retry_wait)

        if status == 'success':
            logging.info('Restoring backup for [{}] successful'.format(full_shard_name))
        else:
            if 'restorestatus' in response and 'exception' in response['restorestatus']:
                exception = response['restorestatus']['exception']
            else:
                exception = 'Unknown'
            raise Exception('Error while restoring backup for [{}] locally: [{}]'
                            .format(full_shard_name, exception))

    def __trigger_local_commit(self):
        for core_name in self.__get_local_cores():
            sharded_regex_match = re.match(REGEX_SHARDED_CORES, core_name)
            single_regex_match = re.match(REGEX_SINGLE_CORE, core_name)
            if sharded_regex_match:
                collection_name = sharded_regex_match.group(1)
            elif single_regex_match:
                collection_name = single_regex_match.group(1)
            else:
                raise Exception('Unknown core name format [{}]'.format(core_name))

            url = LOCAL_URL + '/' + collection_name + '/update?commit=true&wt=json'
            logging.info('Triggering hard commit for [{}] ...'.format(collection_name))
            self.__send_http_request(url)
        logging.info('Waiting for hard commits to finish ...')
        time.sleep(self.__wait_timeout)
        logging.info('Successfully triggered hard commit for all locally hosted collections.')

    def __restore_latest_backup(self, bucket: str, timestamp: str):
        retry = 0
        logging.info('Start restoring backup for timestamp [{}] from S3 bucket [{}].'.format(timestamp, bucket))

        backup_dir = BACKUP_ROOT_DIR + timestamp
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)

        threads = []
        while retry < self.__restore_retry_count:

            for core_name in self.__get_local_cores():
                sharded_regex_match = re.match(REGEX_SHARDED_CORES, core_name)
                single_regex_match = re.match(REGEX_SINGLE_CORE, core_name)
                if sharded_regex_match:
                    collection_name = sharded_regex_match.group(1)
                    shard_name = sharded_regex_match.group(2)
                    shard_backup_dest = backup_dir + '/snapshot.' + collection_name + '_' + shard_name
                elif single_regex_match:
                    collection_name = single_regex_match.group(1)
                    shard_backup_dest = backup_dir + '/snapshot.' + collection_name
                else:
                    raise Exception('Unknown core name format [{}]'.format(core_name))

                if not os.path.isdir(shard_backup_dest):
                    thread = Thread(target=self.__restore_single_backup_task,
                                    args=(bucket, timestamp, collection_name, shard_name, core_name))
                    thread.start()
                    threads.append(thread)
                else:
                    logging.debug('Skipping shard [{}] of collection [{}] since it is already restored.'
                                  .format(shard_name, collection_name))
            time.sleep(self.__restore_retry_wait)
            retry += 1

        # Check that all threads have been finished before finishing
        for thread in threads:
            thread.join()

        logging.info('Finished restoring backup for timestamp [{}] from S3 bucket [{}].'.format(timestamp, bucket))

    def __restore_single_backup_task(self, bucket: str, timestamp: str, collection_name: str, shard_name: str,
                                     core_name: str):
        backup_file_name = 'backup_' + timestamp + '_' + collection_name + '_' + shard_name + '.tar.gz'
        if os.path.isfile(BACKUP_ROOT_DIR + backup_file_name):
            logging.debug('Skipping shard [{}] of collection [{}] since download has already been started.'
                          .format(shard_name, collection_name))
        else:
            shard_backup_dest = BACKUP_ROOT_DIR + timestamp + '/snapshot.' + collection_name + '_' + shard_name
            logging.info('Restoring backup for shard [{}] of collection [{}] ...'.format(shard_name, collection_name))
            self.__download_file_from_s3(bucket, timestamp, backup_file_name, BACKUP_ROOT_DIR)
            self.__unzip_backup_file(BACKUP_ROOT_DIR + backup_file_name, BACKUP_ROOT_DIR + timestamp)
            if os.path.isdir(shard_backup_dest):
                self.__restore_core(core_name, timestamp)
                logging.info('Successfully restored backup for shard [{}] of collection [{}].'
                             .format(shard_name, collection_name))
            else:
                logging.warning('Failed to prepare snapshot directory for shard [{}] of collection [{}].'
                                .format(shard_name, collection_name))
            os.remove(BACKUP_ROOT_DIR + backup_file_name)

    def __get_local_cores(self):
        logging.info('Getting locally hosted cores ...')
        try:
            url = LOCAL_URL + '/admin/cores?action=STATUS&wt=json'
            core_status_response = json.loads(self.__send_http_request(url))
            local_cores = []
            if 'status' in core_status_response:
                local_cores = core_status_response['status'].keys()
            logging.info('Locally hosted cores are: [{}]'.format(local_cores))
            return local_cores
        except Exception as e:
            logging.warning('Could not get locally hosted cores: [{}]'.format(e))
            return []

    @staticmethod
    def __clean_up_backup_dir():
        logging.info('Cleaning up backup directory ...')
        for path in os.listdir(BACKUP_ROOT_DIR):
            if os.path.isdir(BACKUP_ROOT_DIR + path):
                shutil.rmtree(BACKUP_ROOT_DIR + path)
                logging.info('Removed directory [{}]'.format(BACKUP_ROOT_DIR + path))
            elif os.path.isfile(BACKUP_ROOT_DIR + path):
                os.remove(BACKUP_ROOT_DIR + path)
                logging.info('Removed file [{}]'.format(BACKUP_ROOT_DIR + path))
            else:
                logging.warning('Could not delete [{}]'.format(path))

    @staticmethod
    def __send_http_request(url: str):
        try:
            logging.debug('Send HTTP GET request to [{}]'.format(url))
            request = urllib.request.Request(url)
            response = urllib.request.urlopen(request)
            code = response.getcode()
            content = response.read().decode('utf-8')
            response.close()
            if code != 200:
                raise Exception('Received unexpected status code from Solr: [{}]'.format(code))
            return content
        except urllib.error.HTTPError as e:
            if e.code == 504:
                logging.warning('HTTP Timeout, but should have been done anyways.')
            else:
                raise Exception('Failed sending request to Solr [{}]: {}'.format(url, e))
        except Exception as e:
            raise Exception('Failed sending request to Solr [{}]: {}'.format(url, e))

    @staticmethod
    def __download_file_from_s3(bucket, prefix, file_name, destination):
        command = ['aws', 's3', 'cp', 's3://' + bucket + '/' + prefix + '/' + file_name, destination]
        logging.debug('Executing [{}]'.format(' '.join(command)))
        return subprocess.call(command)

    @staticmethod
    def __upload_file_to_s3(bucket, prefix, file_name):
        command = ['aws', 's3', 'cp', file_name, 's3://' + bucket + '/' + prefix + '/']
        logging.debug('Executing [{}]'.format(' '.join(command)))
        return subprocess.call(command)

    @staticmethod
    def __zip_backup_file(file_name, directory, source):
        command = ['tar', '-czf', file_name, '-C', directory, source]
        return subprocess.call(command)

    @staticmethod
    def __unzip_backup_file(file_name, destination):
        command = ['tar', '-xzf', file_name, '-C', destination]
        return subprocess.call(command)


def build_args_parser():
    parser = ArgumentParser(description='SolrCloud Backup CLI')
    parser.add_argument('command', help='Available commands: backup, restore')
    parser.add_argument('-b', '--bucket', help='S3 bucket which contains the backup files')
    parser.add_argument('-t', '--timestamp', help='Backup timestamp in the format of <yyyyMMddHHmm> for restoring data')
    parser.add_argument('-w', '--wait', default=str(DEFAULT_COMMIT_WAIT_IN_SECONDS),
                        help='Wait time after commit in seconds')
    parser.add_argument('-c', '--cron', help='Run as a cron job: hourly, daily, weekly')
    parser.add_argument('--no-cleanup', default=False, help='Do not clean up backup directory afterwards')
    return parser


def backup_cli(cli_args):
    logging.Logger.setLevel(logging.root, LOGGING_LEVEL)

    parser = build_args_parser()
    args = parser.parse_args(cli_args)

    controller = BackupController(int(args.wait))

    if args.command == 'backup':
        if not args.bucket:
            logging.error('No S3 bucket given')
            parser.print_usage()
            return 1
        if args.cron:
            scheduler = BlockingScheduler(timezone=pytz.utc)
            if args.cron == 'hourly':
                scheduler.add_job(controller.create_backup, trigger='cron',
                                  kwargs={'bucket': args.bucket, 'cleanup': not args.no_cleanup},
                                  minute=TIMESTAMP_MINUTE, name='hourly_backup')
                logging.info('Scheduled hourly backup ({} * * * *).'.format(TIMESTAMP_MINUTE))
            elif args.cron == 'daily':
                scheduler.add_job(controller.create_backup, trigger='cron',
                                  kwargs={'bucket': args.bucket, 'cleanup': not args.no_cleanup},
                                  hour=TIMESTAMP_HOUR, minute=TIMESTAMP_MINUTE, name='daily_backup')
                logging.info('Scheduled daily backup ({} {} * * *).'.format(TIMESTAMP_MINUTE, TIMESTAMP_HOUR))
            elif args.cron == 'weekly':
                scheduler.add_job(controller.create_backup, trigger='cron',
                                  kwargs={'bucket': args.bucket, 'cleanup': not args.no_cleanup},
                                  day_of_week=TIMESTAMP_DOW_SCHEDULER, hour=TIMESTAMP_HOUR, minute=TIMESTAMP_MINUTE,
                                  name='weekly_backup')
                logging.info('Scheduled weekly backup ({} {} * * {}).'.format(TIMESTAMP_MINUTE, TIMESTAMP_HOUR,
                                                                              TIMESTAMP_DOW_CRON))
            elif args.cron == 'test':
                scheduler.add_job(controller.create_backup, trigger='cron',
                                  kwargs={'bucket': args.bucket, 'cleanup': not args.no_cleanup},
                                  second='0', name='test_backup')
                logging.info('Scheduled test backup (* * * * *).')
            else:
                logging.error('Unsupported cron interval. Supported intervals are: hourly, daily, weekly')
                return 1
            logging.info('Press Ctrl+{0} to exit'.format('Break' if os.name == 'nt' else 'C'))
            try:
                scheduler.start()
            except (KeyboardInterrupt, SystemExit):
                pass
        else:
            controller.create_backup(bucket=args.bucket)
    elif args.command == 'restore':
        if not args.bucket:
            logging.error('No S3 bucket given')
            parser.print_usage()
            return 1
        timestamp_pattern = re.compile('[0-9]{12}')
        if not args.timestamp or not timestamp_pattern.match(args.timestamp):
            logging.error('No or invalid timestamp for restoring data, format should be <yyyyMMddHHmm>')
            parser.print_usage()
            return 1
        controller.restore_backup(args.bucket, args.timestamp, cleanup=not args.no_cleanup)
    else:
        logging.error('Unknown command: [{}]'.format(args.command))
        parser.print_usage()
        return 1


def main():
    backup_cli(sys.argv[1:])

if __name__ == '__main__':
    main()
