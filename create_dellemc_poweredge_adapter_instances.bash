#!/bin/bash
# list of iDRACs must be in /root/scripts/idracs.txt
# uses existing adapter instance credential as they are all the same, you'll want to change this
# to get your existing dell emc poweredge credential run this command: curl -X GET "https://your_operations_fqdn_here/suite-api/api/credentials?adapterKind=DELL_EMC_COMPUTE_ADAPTER&_no_links=true" -H  "accept: application/json" -H  "Authorization: OpsToken your_token_goes_here"

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

for i in `cat /users/brockp/scripts/idracs.txt`
	do
	echo "Creating Dell EMC PowerEdge Adapter Instance $i in VCF Operations..."
	curl -k -X POST "https://$operations_primary_node/suite-api/api/adapters?extractIdentifierDefaults=false&force=true&_no_links=true" -H  "accept: application/json" -H  "Authorization: OpsToken $token" -H  "Content-Type: application/json" -d "{  \"name\" : \"Dell EMC PowerEdge Adapter Instance $i\",  \"description\" : \"Dell EMC PowerEdge Adapter Instance $i\",  \"adapterKindKey\" : \"DELL_EMC_COMPUTE_ADAPTER\",  \"resourceIdentifiers\" : [ {    \"name\" : \"management_ip\",    \"value\" : \"$i\"  } , {    \"name\" : \"snmp_port\",    \"value\" : \"161\"  } , {    \"name\" : \"retries\",    \"value\" : \"2\"  } , {    \"name\" : \"timeout_interval\",    \"value\" : \"30\"  } , {    \"name\" : \"socket_timeout_interval\",    \"value\" : \"1\"  } , {    \"name\" : \"discovery_timeout\",    \"value\" : \"120\"  } , {    \"name\" : \"collection_timeout\",    \"value\" : \"300\"  } , {    \"name\" : \"max_threads\",    \"value\" : \"30\"  } , {    \"name\" : \"include_snmp_alerts\",    \"value\" : \"false\"  } , {    \"name\" : \"alert_cancel_hours\",    \"value\" : \"24\"  } , {    \"name\" : \"support_autodiscovery\",    \"value\" : \"true\"  }],\"credential\": {    \"id\": \"b5798a73-82ec-43ea-b273-64039cc0dc1d\"  }}" 1>/dev/null 2>/dev/null
	echo 
	curl -k -X GET "https://$operations_primary_node/suite-api/api/adapters?adapterKindKey=DELL_EMC_COMPUTE_ADAPTER&_no_links=true" -H  "accept: application/json" -H  "Authorization: OpsToken $token" > /tmp/a.out 2>/dev/null
	adapter_instance_id=`jq -r '.adapterInstancesInfoDto[] | "\(.resourceKey.name):\(.id)"' /tmp/a.out | grep $i | awk -F ":" '{print $2}'`
	echo "This is the ID of the Dell EMC PowerEdge Adapter Instance $i you just created: $adapter_instance_id"
	echo
	echo "Would you like to start the Adapter Instance now?  yes/no"
	read start
		if [ "$start" == "yes" ] 
			then 
				echo "Starting Dell EMC PowerEdge Adapter Instance $i..."
				echo 
				curl -k -X PUT "https://$operations_primary_node/suite-api/api/adapters/$adapter_instance_id/monitoringstate/start?_no_links=true" -H  "accept: */*" -H  "Authorization: OpsToken $token" 1>/dev/null 2>/dev/null
			else
				echo "Not starting Dell EMC PowerEdge Adapter Instance $i..."
				echo 
		fi
done
