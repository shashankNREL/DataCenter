#!/bin/bash
# Note: 
# recommend setting up ~/.ssh/config to specify User and HostName
# Host mymachine
#    User jdoe
#    HostName mymachine.com

machine="mymachine"
mkdir -p jobprofile slurm/jobcomplete slurm/joblive

if [ -n "$1" ]; then
    DATE=$1
else
    DATE="2024-01-19"
fi

DPATH=/path/to/data/lake

/usr/bin/scp -r $machine:$DPATH/jobprofile/jobprofile/date=$DATE jobprofile
/usr/bin/scp -r $machine:$DPATH/slurm/joblive/date=$DATE slurm/joblive
