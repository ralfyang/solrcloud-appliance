#!/bin/bash
BACKUP_INTERVAL=test
SOLR_BACKUP_BUCKET=zalando-riskmgmt-eu-west-1-test-fraud-detection-solr-backup
RESTORE_LATEST_BACKUP=False

cp /solr.xml /data/
mkdir -p /data/logs

function core_properties() {
cat << _EOF_	
name=${1}
config=solrconfig.xml
schema=schema.xml
dataDir=data
_EOF_
}
 
CONFIGS_DIR=/opt/solr/configs
CORE_DIRS=/data
COLLECTIONS=$(find ${CONFIGS_DIR} -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf "%f\n")

for col in ${COLLECTIONS[*]}; do
   mkdir -p ${CORE_DIRS}/${col}
   cp -r ${CONFIGS_DIR}/${col} ${CORE_DIRS}/${col}/conf   
   core_properties ${col} > ${CORE_DIRS}/${col}/core.properties 
done

MEM_JAVA_PERCENT=20
MEM_TOTAL_KB=$(cat /proc/meminfo | grep MemTotal | awk '{print $2}')
MEM_JAVA_KB=$(($MEM_TOTAL_KB * $MEM_JAVA_PERCENT / 100))

JAVA_OPTS="$JAVA_OPTS -javaagent:/opt/jolokia-jvm-1.3.2-agent.jar=port=8778,host=0.0.0.0"
JAVA_OPTS="$JAVA_OPTS -Xloggc:/data/logs/solr_gc.log"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.port=48983"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.authenticate=false"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.ssl=false"

SOLR_BACKUP_DIR=/data/backup
mkdir -p $SOLR_BACKUP_DIR

# Start backup job as background process
if [ -n "$BACKUP_INTERVAL" ] && [[ "${BACKUP_INTERVAL}" =~ ^(weekly|daily|hourly|test)$ ]]
then
    [[ -z "${SOLR_BACKUP_BUCKET}" ]] && { echo "Parameter SOLR_BACKUP_BUCKET is empty" ; exit 1; }
    echo "Start backup job as background process"
    nohup ./scripts/solrcloud_backup.py -b "${SOLR_BACKUP_BUCKET}" -c "${BACKUP_INTERVAL}" backup &
else
    echo "Backup job is not configured to be started"
fi

# Restore latest available backup
if [ -n "$RESTORE_LATEST_BACKUP" ] && [[ "${RESTORE_LATEST_BACKUP}" =~ ^[tT][rR][uU][eE]$ ]]
then
    [[ -z "${SOLR_BACKUP_BUCKET}" ]] && { echo "Parameter SOLR_BACKUP_BUCKET is empty" ; exit 1; }
    export LATEST=$(aws s3 ls s3://"${SOLR_BACKUP_BUCKET}"/ | grep -E "[0-9]+/$" | sort -z | sed 's/.*PRE \([0-9]\+\)\//\1/' | tail -2)
    echo "Start to restore latest backup [${LATEST}]"
    nohup ./scripts/solrcloud_backup.py -b "${SOLR_BACKUP_BUCKET}" -t "${LATEST}" restore &
else
    echo "Startup with empty index, no backup will be restored"
fi

# Start solr standalone instance
echo
echo "You can access Solr via http://$(hostname -i):8983/solr" >&2
echo 
/opt/solr/bin/solr start -f -s /data -m ${MEM_JAVA_KB}k -a "${JAVA_OPTS}"
