#!/bin/bash
# list of iLOs must be in /root/scripts/ilos.txt
# uses existing adapter instance credential as they are all the same, you'll want to change this

echo "What is the IP of your Operations primary node?"
read operations_primary_node
echo

echo "What is the local admin password?"
read admin_password
echo

#get vcf operations bearer token
curl -k -X POST "https://$operations_primary_node/suite-api/api/auth/token/acquire?_no_links=true" -H  "accept: application/json" -H  "Content-Type: application/json" -d "{  \"username\" : \"admin\",  \"authSource\" : \"local\",  \"password\" : \"$admin_password\"}" > /tmp/token.txt 2>/dev/null

echo "This is the Operations Token to be used in all subsequent API calls:" `cat /tmp/token.txt | awk -F'"' '{print $4}'` 
echo 

token=`cat /tmp/token.txt | awk -F'"' '{print $4}'`

#get vcf operations collector groups
curl -k -X GET "https://$operations_primary_node/suite-api/api/collectorgroups?_no_links=true" -H  "accept: application/json" -H  "Authorization: OpsToken $token" > /tmp/b.out 2>/dev/null

#format collector groups to be used later
jq -r '.collectorGroups | to_entries[] | "\(.key + 1). \(.value.name) : collectorId \(.value.collectorId[0]) : \(.value.id)"' /tmp/b.out > /tmp/c.out

for i in `cat /users/brockp/scripts/ilos.txt`
	do
	echo "Creating HPE Proliant Adapter Instance $i in VCF Operations..."
	curl -k -X POST "https://$operations_primary_node/suite-api/api/adapters?extractIdentifierDefaults=false&force=true&_no_links=true" -H  "accept: application/json" -H  "Authorization: OpsToken $token" -H  "Content-Type: application/json" -d "{  \"name\": \"HPE Proliant Adapter Instance $i\",  \"description\": \"HPE Proliant Adapter Instance $i\",  \"collectorId\": \"1\",  \"adapterKindKey\": \"HPComputeAdapter\",  \"resourceKindKey\": \"hpcompute_adapter_instance\",  \"physicalDatacenterId\": \"\",  \"credential\": {    \"id\": \"c2bb102b-96d5-4e40-9e67-79c7c82e9f95\"  },  \"resourceIdentifiers\": [    {      \"name\": \"collect_events\",      \"value\": \"Yes\"    },    {      \"name\": \"discover_ports\",      \"value\": \"No\"    },    {      \"name\": \"host\",      \"value\": \"$i\"    },    {      \"name\": \"minimum_event_severity\",      \"value\": \"Info\"    },    {      \"name\": \"port\",      \"value\": \"443\"    },    {      \"name\": \"ssl_config\",      \"value\": \"No\"    },    {      \"name\": \"support_autodiscovery\",      \"value\": \"True\"    },    {      \"name\": \"threadpool_size\",      \"value\": \"10\"    },    {      \"name\": \"timeout\",      \"value\": \"300\"    },    {      \"name\": \"use_sha256_key_exchange\",      \"value\": \"Yes\"    }  ]}" 1>/dev/null 2>/dev/null
	echo 
 	curl -k -X GET "https://$operations_primary_node/suite-api/api/adapters?adapterKindKey=HPComputeAdapter&_no_links=true" -H  "accept: application/json" -H  "Authorization: OpsToken $token" > /tmp/a.out 2>/dev/null
	adapter_instance_id=`jq -r '.adapterInstancesInfoDto[] | "\(.resourceKey.name):\(.id)"' /tmp/a.out | grep $i | awk -F ":" '{print $2}'`
	echo "This is the ID of the HPE Proliant Adapter Instance $i you just created: $adapter_instance_id"
	echo ""

	# Capture collector group name and collector group id
	name=$(jq -r --argjson choice "$((choice-1))" '.collectorGroups[$choice].name' "/tmp/b.out")
	collectorId=$(jq -r --argjson choice "$((choice-1))" '.collectorGroups[$choice].collectorId[0]' "/tmp/b.out")
	
	# Display enumerated collector groups
	echo "Available Collector Groups:"
	jq -r '.collectorGroups | to_entries[] | "\(.value.name) : collectorId \(.value.collectorId[0])"' "/tmp/b.out"
	echo

	# Ask user which Collector Group they would like to use
	read -p "Enter the collectorId of the Collector Group you want to use:" collectorId

	# Display what user selected
	echo
	echo "You selected: CollectorGroup=$name with CollectorGroupId=$collectorId "
	echo
	echo "Adjusting the Collector Group for HPE Proliant Adapter Instance $i..."
	curl -k -X PUT "https://$operations_primary_node/suite-api/api/adapters?_no_links=true" -H  "accept: application/json" -H  "Authorization: OpsToken $token" -H  "Content-Type: application/json" -d "{      \"resourceKey\": {        \"name\": \"HPE Proliant Adapter Instance $i\",                \"adapterKindKey\": \"HPComputeAdapter\",        \"resourceKindKey\": \"hpcompute_adapter_instance\",        \"resourceIdentifiers\": [          {            \"identifierType\": {              \"name\": \"collect_events\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": false            },            \"value\": \"Yes\"          },          {            \"identifierType\": {              \"name\": \"discover_ports\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": false            },            \"value\": \"No\"          },          {            \"identifierType\": {              \"name\": \"host\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": true            },            \"value\": \"$i\"          },          {            \"identifierType\": {              \"name\": \"minimum_event_severity\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": false            },            \"value\": \"Info\"          },          {            \"identifierType\": {              \"name\": \"port\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": false            },            \"value\": \"443\"          },          {            \"identifierType\": {              \"name\": \"ssl_config\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": false            },            \"value\": \"No\"          },          {            \"identifierType\": {              \"name\": \"support_autodiscovery\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": false            },            \"value\": \"True\"          },          {            \"identifierType\": {              \"name\": \"threadpool_size\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": false            },            \"value\": \"10\"          },          {            \"identifierType\": {              \"name\": \"timeout\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": false            },            \"value\": \"300\"          },          {            \"identifierType\": {              \"name\": \"use_sha256_key_exchange\",              \"dataType\": \"STRING\",              \"isPartOfUniqueness\": false            },            \"value\": \"Yes\"          }        ]      },      \"collectorId\": \"$collectorId\",      \"credentialInstanceId\": \"c2bb102b-96d5-4e40-9e67-79c7c82e9f95\",      \"monitoringInterval\": 5,      \"numberOfMetricsCollected\": 0,      \"numberOfResourcesCollected\": 1,      \"lastHeartbeat\": 1745266817521,      \"lastCollected\": 1745266523142,      \"messageFromAdapterInstance\": \"Caught an exception while collecting: Did not collect any resources\",      \"id\": \"$adapter_instance_id\"    }" 1>/dev/null 2>/dev/null
	echo
	echo "Would you like to start the Adapter Instance now?  yes/no"
	read start
		if [ "$start" == "yes" ] 
			then 
				echo "Starting HPE Proliant Adapater Instance $i..."
				echo 
				curl -k -X PUT "https://$operations_primary_node/suite-api/api/adapters/$adapter_instance_id/monitoringstate/start?_no_links=true" -H  "accept: */*" -H  "Authorization: OpsToken $token"			
			else
				echo "Not starting HPE ProLiant Adapter Instance $i..."
				echo 
		fi
done
