#!/usr/bin/bash

KEY_DIR=/client-configs/keys
OUTPUT_DIR=/client-configs/files
BASE_CONFIG=/client-configs/client_base.conf
EASY_RSA_DIR=/easy-rsa
PKI_DIR=/easy-rsa/pki

help() {
  echo "Usage: add_client.sh [-h] <client name>"
  echo "   -h : show help message"
  echo "   client name is a mandatory parameter."
  echo "   Open VPN config is saved in ${OUTPUT_DIR}/<client name>.ovpn"
}

while getopts ":h" option; do
  case $option in
    h)help; exit;;
    ?)help; exit;;
  esac
done

shift $((OPTIND-1))

if [ -z $1 ]; then
  help
  exit
fi

CLIENT_NAME=$1

# Prepare client keys

cd ${EASY_RSA_DIR}
echo | ./easyrsa gen-req ${CLIENT_NAME} nopass
cp ${PKI_DIR}/private/${CLIENT_NAME}.key ${KEY_DIR}
#./easyrsa import-req /tmp/${CLIENT_NAME}.req ${CLIENT_NAME}
echo "yes" | ./easyrsa sign-req client ${CLIENT_NAME}
cp ${PKI_DIR}/issued/${CLIENT_NAME}.crt ${KEY_DIR}

# Compose keys and configurations into ovpn config

cat ${BASE_CONFIG} \
    <(echo -e '<ca>') \
    ${PKI_DIR}/ca.crt \
    <(echo -e '</ca>\n<cert>') \
    ${KEY_DIR}/${1}.crt \
    <(echo -e '</cert>\n<key>') \
    ${KEY_DIR}/${1}.key \
    <(echo -e '</key>\n<tls-crypt>') \
    ${EASY_RSA_DIR}/ta.key \
    <(echo -e '</tls-crypt>') \
    > ${OUTPUT_DIR}/${1}.ovpn
