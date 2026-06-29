BASE_DIR=~/git/experiments/client_autoscribe
APP=rpa_interface:app
PID_FILE=~/logs/rpa_api.pid

if [ $# -ne 1 ]
then
    echo "Please provide option [start/stop/restart]"
elif [ $1 == "start" ]
then
    cd ${BASE_DIR} && gunicorn ${APP}
elif [ $1 == "stop" ]
then
    pkill -F ${PID_FILE}
elif [ $1 == "restart" ]
then
    pkill -HUP -F ${PID_FILE}
else
    echo "Please provide option [start/stop/restart]"
fi
