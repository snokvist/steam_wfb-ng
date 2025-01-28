#include <fcntl.h>
#include <sodium.h>
#include <string.h>
#include <ctype.h>
#include <stdio.h>

int is_valid_filename(const char *filename) {
    // Allow only alphanumeric characters, dots, dashes, underscores, and slashes
    for (const char *c = filename; *c != '\0'; c++) {
        if (!(isalnum(*c) || *c == '.' || *c == '-' || *c == '_' || *c == '/')) {
            return 0;
        }
    }
    return 1;
}

int main(int argc, char **argv) {
    unsigned char drone_publickey[crypto_box_PUBLICKEYBYTES];
    unsigned char drone_secretkey[crypto_box_SECRETKEYBYTES];
    unsigned char gs_publickey[crypto_box_PUBLICKEYBYTES];
    unsigned char gs_secretkey[crypto_box_SECRETKEYBYTES];
    FILE *fp;

    if (argc < 2 || argc > 3) {
        printf("Usage: %s <passphrase> [output_filename]\n", argv[0]);
        return 1;
    }

    if (sodium_init() < 0) {
        printf("Libsodium init failed\n");
        return 1;
    }

    const char *seed = argv[1];
    printf("Using passphrase: %s\n", seed);

    if (crypto_box_seed_keypair(drone_publickey, drone_secretkey, seed) != 0 ||
        crypto_box_seed_keypair(gs_publickey, gs_secretkey, seed) != 0) {
        printf("Unable to generate keys\n");
        return 1;
    }

    const char *key = argc == 3 ? argv[2] : "/etc/gs.key";

    if (!is_valid_filename(key)) {
        printf("Invalid filename: %s\n", key);
        return 1;
    }

    if ((fp = fopen(key, "w")) == NULL) {
        printf("Unable to save: %s\n", key);
        return 1;
    }

    fwrite(gs_secretkey, crypto_box_SECRETKEYBYTES, 1, fp);
    fwrite(drone_publickey, crypto_box_PUBLICKEYBYTES, 1, fp);
    fclose(fp);

    printf("Groundstation keypair saved: %s\n", key);

    return 0;
}
