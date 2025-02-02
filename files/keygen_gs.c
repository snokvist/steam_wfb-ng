#include <stdio.h>
#include <stdlib.h>
#include <sodium.h>
#include <string.h>

int main(int argc, char **argv) {
    unsigned char publickey[crypto_box_PUBLICKEYBYTES];
    unsigned char secretkey[crypto_box_SECRETKEYBYTES];
    FILE *fp;

    // Ensure we have at least the passphrase; allow an optional key file path.
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "Usage: %s <passphrase> [key_file_path]\n", argv[0]);
        return 1;
    }

    if (sodium_init() < 0) {
        fprintf(stderr, "Libsodium init failed\n");
        return 1;
    }

    // Copy the passphrase into seed.
    char seed[32];
    strncpy(seed, argv[1], sizeof(seed));
    seed[sizeof(seed) - 1] = '\0';  // Ensure null-termination
    printf("Using passphrase: %s\n", seed);

    if (crypto_box_seed_keypair(publickey, secretkey, seed) != 0) {
        fprintf(stderr, "Unable to generate key\n");
        return 1;
    }

    // Use the second argument if provided, otherwise use the default.
    const char *key_path = (argc == 3) ? argv[2] : "/etc/gs.key";

    if ((fp = fopen(key_path, "w")) == NULL) {
        fprintf(stderr, "Unable to save: %s\n", key_path);
        return 1;
    }

    fwrite(secretkey, crypto_box_SECRETKEYBYTES, 1, fp);
    fwrite(publickey, crypto_box_PUBLICKEYBYTES, 1, fp);
    fclose(fp);

    printf("Key saved: %s\n", key_path);

    return 0;
}
