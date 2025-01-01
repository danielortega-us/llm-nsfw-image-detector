import base64

def encode_to_base64(src: str) -> str:
    encoded_bytes = base64.b64encode(src.encode("utf-8"))
    return encoded_bytes.decode("utf-8")

def decode_from_base64(src: str) -> str:
    decoded_bytes = base64.b64decode(src.encode("utf-8"))
    return decoded_bytes.decode("utf-8")

with open("fabricate.json", "r", encoding = "utf-8") as fp:
    fab = fp.read()

fab_enc = encode_to_base64(fab)

with open("nsfw.conf", "w", encoding = "utf-8") as fp:
    fp.write(fab_enc)
