#!/bin/bash
# Background polling loop that keeps wireless ADB devices connected.
# Runs wireless_connect.sh every REMOTE_ADB_POLLING_SEC seconds (default: 5).
# Triggered automatically when REMOTE_ADB=true.

if [ ! -z "${REMOTE_ADB}" ]; then

	if [ -z "${REMOTE_ADB_POLLING_SEC}" ]; then
		REMOTE_ADB_POLLING_SEC=5
	fi

	function connect() {
		while true; do
			# Avoid immediate run on first iteration
			sleep ${REMOTE_ADB_POLLING_SEC}
			wireless_connect.sh
		done
	}

	( trap "true" HUP ; connect ) >/dev/null 2>/dev/null </dev/null & disown

fi
