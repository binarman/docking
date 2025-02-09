SCRIPT_DIR=$(dirname "$(realpath -s "$0")")

IMAGE_NAME="pantools-img"

cp $HOME/.Xauthority xauth
mkdir $HOME/pantools-share
docker build -t "$IMAGE_NAME" --build-arg USER_ID=$(id -u) -f "$SCRIPT_DIR/dockerfile" "$SCRIPT_DIR"
docker run --rm -it --net=host --env DISPLAY=$DISPLAY --env XAUTHORITY=/home/user/.Xauthority -v /tmp/.X11-unix:/tmp/.X11-unix -v $HOME/pantools-share:/share --rm "$IMAGE_NAME" hugin
