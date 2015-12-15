#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
import urllib.request

EXHIBITOR_API = os.getenv('ZK_API')
if not EXHIBITOR_API:
    print("Missing environment variable [ZK_API].")
    sys.exit(1)


def get_cluster_list():
    url = EXHIBITOR_API + '/cluster/list'
    try:
        request = urllib.request.Request(url)
        response = urllib.request.urlopen(request)
        code = response.getcode()
        content = response.readall().decode('utf-8')
        response.close()
        if code != 200:
            print('ERROR Received unexpected status code from Exhibitor: [{}]'.format(code))
            exit(1)
        return json.loads(content)
    except Exception as e:
        print('ERROR Failed sending request to Exhibitor [{}]: {}'.format(url, e))
        exit(1)


cluster_list = get_cluster_list()
servers = cluster_list['servers']
port = cluster_list['port']

output = ','.join(map(lambda x: x + ':' + str(port), servers))
# Output list of currently active Zookeeper servers
print(output)
