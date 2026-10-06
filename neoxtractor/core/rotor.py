"""Provides Rotor class."""

from typing import Any


class Rotor(object):
    """
    Rotor implements a simple rotor-based symmetric encryption and decryption algorithm.
    Attributes:
        n_rotors (int): Number of rotors to use in the algorithm.
        key (str): The key used to initialize the rotor positions and permutations.
        rotors (tuple or None): Stores the rotor permutations and positions.
        positions (list): Tracks the current positions for encryption and decryption.
    Methods:
        __init__(key, n_rotors=6):
            Initializes the Rotor with a given key and number of rotors.
        setkey(key):
            Sets the key for the rotor algorithm and resets internal state.
        encrypt(buf):
            Encrypts the given bytes-like object `buf` and returns the encrypted bytes.
        decrypt(buf):
            Decrypts the given bytes-like object `buf` and returns the decrypted bytes.
        cryptmore(buf, do_decrypt):
            Core method for encrypting or decrypting the buffer `buf`.
            If `do_decrypt` is True, performs decryption; otherwise, encryption.
        get_rotors(do_decrypt):
            Retrieves or initializes the rotor permutations and positions for encryption or decryption.
        random_func(key):
            Returns a pseudorandom function seeded with the provided key,
            used for rotor permutation and position generation.
    """

    # starts the rotor
    def __init__(self, key: str, n_rotors=6):
        self.n_rotors: int = n_rotors
        self.key: str = key
        self.rotors: tuple = ()
        self.positions: list[list[int] | None]

    # encrypts the buffer
    def encrypt(self, buf: bytes) -> bytes:
        self.positions[0] = None
        return self.cryptmore(buf, False)

    # decrypts the buffer
    def decrypt(self, buf: bytes) -> bytes:
        self.positions[1] = None
        return self.cryptmore(buf, True)

    # def for the encryption / decryption
    def cryptmore(self, buf: bytes, do_decrypt: bool) -> bytes:
        size, nr, rotors, pos = self.get_rotors(do_decrypt)
        outbuf = b""
        for c in buf:
            if do_decrypt:
                for i in range(nr - 1, -1, -1):
                    c = pos[i] ^ rotors[i][c]
            else:
                for i in range(nr):
                    c = rotors[i][c ^ pos[i]]
            outbuf = outbuf + c.to_bytes(1, "big")

            pnew = 0
            for i in range(nr):
                pnew = ((pos[i] + (pnew >= size)) & 0xFF) + rotors[i][size]
                pos[i] = pnew % size

        return outbuf

    # gets the rotors position for the encryption / decryption
    def get_rotors(
        self, do_decrypt: bool
    ) -> tuple[int, int, tuple[tuple[int, ...]] | Any, list[int]]:
        nr = self.n_rotors
        rotors = self.rotors
        positions: list[int] | None = self.positions[do_decrypt]

        if positions is None:
            if rotors:
                positions = list(rotors[3])
            else:
                self.size = size = int(256)
                id_rotor = list(range(size + 1))

                rand = self.random_func(self.key)
                E: list[tuple[int, ...]] = []
                D: list[tuple[int, ...]] = []
                positions = []
                for i in range(nr):
                    i = size
                    positions.append(rand(i))
                    erotor = id_rotor[:]
                    drotor = id_rotor[:]
                    drotor[i] = erotor[i] = 1 + 2 * rand(i // 2)  # increment
                    while i > 1:
                        r = rand(i)
                        i -= 1
                        er = erotor[r]
                        erotor[r] = erotor[i]
                        erotor[i] = er
                        drotor[er] = i
                    drotor[erotor[0]] = 0
                    E.append(tuple(erotor))
                    D.append(tuple(drotor))
                self.rotors = rotors = (tuple(E), tuple(D), size, tuple(positions))
            self.positions[do_decrypt] = positions
        return rotors[2], nr, rotors[do_decrypt], positions

    # pseudorandom full algorithm with a key
    def random_func(self, key: str):
        mask = 0xFFFF
        x = 995
        y = 576
        z = 767
        for c in map(ord, key):
            x = ((x << 3 | x >> 13) + c) & mask
            y = ((y << 3 | y >> 13) ^ c) & mask
            z = ((z << 3 | z >> 13) - c) & mask

        maxpos = mask >> 1
        mask += 1
        if x > maxpos:
            x -= mask
        if y > maxpos:
            y -= mask
        if z > maxpos:
            z -= mask

        y |= 1

        x = 171 * (int(x) % 177) - 2 * (int(x) // 177)
        y = 172 * (int(y) % 176) - 35 * (int(y) // 176)
        z = 170 * (int(z) % 178) - 63 * (int(z) // 178)
        if x < 0:
            x += 30269
        if y < 0:
            y += 30307
        if z < 0:
            z += 30323

        # pseudorandom algorithm with an X Y Z seed
        def rand(n: int, seed: list[tuple[int, int, int]] = [(x, y, z)]):
            x, y, z = seed[0]
            seed[0] = ((171 * x) % 30269, (172 * y) % 30307, (170 * z) % 30323)
            return int(int((x / 30269 + y / 30307 + z / 30323) * n) % n)

        return rand
