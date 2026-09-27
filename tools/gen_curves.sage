# Regenerates bench/curves.json. Run with: sage tools/gen_curves.sage
#
# Every ladder curve is derived from SHA-256 of a public label, so nobody
# (including us) picked a curve with a secret trapdoor. Each curve must be:
#   - prime order n (no Pohlig-Hellman)
#   - n != p (no Smart / anomalous-curve attack)
#   - embedding degree > 100 (no MOV / Frey-Rueck)
# The standard curves at the top of the ladder are taken as published.

import hashlib, json, os

BITS = [24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224]
MIN_EMBEDDING_DEGREE = 100


def h(label):
    return int.from_bytes(hashlib.sha256(label.encode()).digest(), 'big')


def expand(label, bits):
    out, i = 0, 0
    while out.bit_length() < bits + 64:
        out = (out << 256) | h(f"{label}/{i}")
        i += 1
    return out


def embedding_degree_exceeds(p, n, bound):
    x = Mod(p, n)
    y = x
    for _ in range(bound):
        if y == 1:
            return False
        y *= x
    return True


def generator(E, label):
    F = E.base_field()
    i = 0
    while True:
        x = F(expand(f"{label}/G/{i}", F.characteristic().nbits()))
        i += 1
        if E.is_x_coord(x):
            P = E.lift_x(x)
            return min(P, -P, key=lambda Q: int(Q[1]))


def ladder_curve(bits):
    label = f"ecdlp-bench/v1/{bits}"
    p = next_prime(2**(bits - 1) + expand(f"{label}/p", bits) % 2**(bits - 1))
    F = GF(p)
    ctr = 0
    while True:
        a = F(expand(f"{label}/a/{ctr}", bits))
        b = F(expand(f"{label}/b/{ctr}", bits))
        ctr += 1
        if 4 * a**3 + 27 * b**2 == 0:
            continue
        E = EllipticCurve(F, [a, b])
        n = E.order()
        if not is_prime(n) or n == p:
            continue
        if not embedding_degree_exceeds(p, n, MIN_EMBEDDING_DEGREE):
            continue
        G = generator(E, label)
        return dict(name=f"bench-{bits}", bits=int(n.nbits()), p=int(p), a=int(a), b=int(b),
                    n=int(n), gx=int(G[0]), gy=int(G[1]), seed=f"{label} (counter {ctr - 1})")


STANDARD = ["secp256k1", "prime256v1", "secp384r1", "secp521r1"]
DISPLAY = {"secp256k1": "secp256k1", "prime256v1": "P-256", "secp384r1": "P-384", "secp521r1": "P-521"}


def openssl_params(name):
    # Take the published parameters from OpenSSL rather than retyping them.
    import subprocess
    text = subprocess.run(["openssl", "ecparam", "-name", name, "-param_enc", "explicit", "-text", "-noout"],
                          capture_output=True, text=True, check=True).stdout
    fields, key = {}, None
    for line in text.splitlines():
        head = line.split(":")[0].strip()
        if not line.startswith(" ") and head in ("Prime", "A", "B", "Generator (uncompressed)", "Order"):
            inline = line.split(":", 1)[1].strip()  # small values print inline as "7 (0x7)"
            key, fields[head] = head, inline.split("(0x")[1].rstrip(")") if "(0x" in inline else inline
        elif not line.startswith(" "):
            key = None
        elif key:
            fields[key] += line.strip().replace(":", "")
    g = fields["Generator (uncompressed)"]
    assert g[:2] == "04"
    half = (len(g) - 2) // 2
    return dict(name=DISPLAY[name], p=int(fields["Prime"], 16), a=int(fields["A"], 16), b=int(fields["B"], 16),
                n=int(fields["Order"], 16), gx=int(g[2:2 + half], 16), gy=int(g[2 + half:], 16))


def check_standard(c):
    F = GF(c["p"])
    E = EllipticCurve(F, [c["a"], c["b"]])
    G = E(c["gx"], c["gy"])
    assert is_prime(c["n"]) and c["n"] * G == 0, c["name"]
    assert embedding_degree_exceeds(c["p"], c["n"], MIN_EMBEDDING_DEGREE), c["name"]
    c["a"] = int(F(c["a"]))
    c["bits"] = int(Integer(c["n"]).nbits())
    c["seed"] = "published standard"
    return c


def write(curves):
    curves = sorted(curves, key=lambda c: c["bits"])
    with open("bench/curves.json.tmp", "w") as f:
        json.dump({"min_embedding_degree": int(MIN_EMBEDDING_DEGREE), "curves": curves}, f, indent=2)
        f.write("\n")
    os.replace("bench/curves.json.tmp", "bench/curves.json")


curves = []
for name in STANDARD:  # fail fast on the standard curves before the long point counts
    check_standard(openssl_params(name))
for bits in BITS:
    c = ladder_curve(bits)
    print(c["name"], c["seed"], flush=True)
    curves.append(c)
    write(curves)
for name in STANDARD:
    c = check_standard(openssl_params(name))
    curves.append(c)
    print(c["name"], "ok", flush=True)

write(curves)
