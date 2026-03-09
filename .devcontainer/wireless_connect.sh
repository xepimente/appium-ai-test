#!/bin/bash
# Connect to Android devices wirelessly via ADB.
# Reads ANDROID_DEVICES env var — comma-separated list of host:port entries.
# Example: ANDROID_DEVICES=192.168.1.100:5555,192.168.1.101:5555

if [ ! -z "${ANDROID_DEVICES}" ]; then
	connected_devices=$(adb devices 2>/dev/null)
	IFS=',' read -r -a array <<<"${ANDROID_DEVICES}"
	for i in "${!array[@]}"; do
		array_device=$(echo ${array[$i]} | tr -d " ")
		# Only connect if not already in the device list
		if [[ ${connected_devices} != *${array_device}* ]]; then
			echo "Connecting to: ${array_device}"
			adb connect ${array_device} >/dev/null 2>/dev/null
			# Give time to finish connection
			sleep 2
			adb devices
			echo "Success!"
		fi
	done
fi
