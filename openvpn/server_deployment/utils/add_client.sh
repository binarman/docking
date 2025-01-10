CLIENT_NAME=$1

KEY_DIR=/client-configs/keys
OUTPUT_DIR=/client-configs/files
BASE_CONFIG=/client-configs/client_base.conf

# Prepare client keys

cd /easy-rsa
./easyrsa gen-req ${CLIENT_NAME} nopass
cp pki/private/${CLIENT_NAME}.key ${KEY_DIR}
#./easyrsa import-req /tmp/${CLIENT_NAME}.req ${CLIENT_NAME}
./easyrsa sign-req client ${CLIENT_NAME}
cp pki/issued/${CLIENT_NAME}.crt ${KEY_DIR}

# Compose keys and configurations into ovpn config

cat ${BASE_CONFIG} \
    <(echo -e '<ca>') \
    ${KEY_DIR}/ca.crt \
    <(echo -e '</ca>\n<cert>') \
    ${KEY_DIR}/${1}.crt \
    <(echo -e '</cert>\n<key>') \
    ${KEY_DIR}/${1}.key \
    <(echo -e '</key>\n<tls-crypt>') \
    ${KEY_DIR}/ta.key \
    <(echo -e '</tls-crypt>') \
    > ${OUTPUT_DIR}/${1}.ovpn
