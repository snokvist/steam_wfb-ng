#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>

#define DEFAULT_SERVER_IP "10.5.99.2"
#define DEFAULT_SERVER_PORT 5555
#define BUFFER_SIZE 8192
#define OUTPUT_DIR "/tmp/bind"
#define OUTPUT_FILE "/tmp/bind/bind.tar.gz"
#define DEFAULT_LISTEN_DURATION 60  // Listen duration in seconds

void print_help() {
    fprintf(stderr, "Usage: wfb_bind_rcv [OPTIONS]\n");
    fprintf(stderr, "Options:\n");
    fprintf(stderr, "  --ip <address>          Set server IP address (default: %s)\n", DEFAULT_SERVER_IP);
    fprintf(stderr, "  --port <number>         Set server port (default: %d)\n", DEFAULT_SERVER_PORT);
    fprintf(stderr, "  --listen-duration <sec> Set duration to listen before closing (default: %d seconds)\n", DEFAULT_LISTEN_DURATION);
    fprintf(stderr, "  --force-listen          Continue listening for the full duration even after a successful BIND command\n");
    fprintf(stderr, "  --help                  Show this help message\n");
}

/**
 * Ensures /tmp/bind directory exists.
 */
void ensure_output_directory() {
    struct stat st = {0};
    if (stat(OUTPUT_DIR, &st) == -1) {
        if (mkdir(OUTPUT_DIR, 0777) != 0) {
            perror("Failed to create output directory");
            exit(1);
        }
    }
}

// Base64 decoding function; writes decoded data to OUTPUT_FILE.
int base64_decode_and_save(const char *input, size_t input_length) {
    FILE *output_file = fopen(OUTPUT_FILE, "wb");
    if (!output_file) {
        fprintf(stderr, "ERR\tFailed to open output file\n");
        return 2;
    }

    unsigned char decode_buffer[BUFFER_SIZE];
    int val = 0, valb = -8;
    size_t out_len = 0;

    for (size_t i = 0; i < input_length; i++) {
        char c = input[i];
        if (c == '=' || c == '\n' || c == '\r')
            continue;
        char *pos = strchr("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", c);
        if (pos == NULL)
            continue;
        val = (val << 6) + (pos - "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/");
        valb += 6;
        if (valb >= 0) {
            decode_buffer[out_len++] = (val >> valb) & 0xFF;
            valb -= 8;
        }
        if (out_len >= BUFFER_SIZE) {
            fwrite(decode_buffer, 1, out_len, output_file);
            out_len = 0;
        }
    }

    if (out_len > 0) {
        fwrite(decode_buffer, 1, out_len, output_file);
    }

    fclose(output_file);
    return 0;
}

// Returns elapsed time (in seconds) since the provided start time.
static double elapsed_time_sec(const struct timespec *start) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    double seconds = (double)(now.tv_sec - start->tv_sec);
    double nsecs   = (double)(now.tv_nsec - start->tv_nsec) / 1e9;
    return seconds + nsecs;
}

int main(int argc, char *argv[]) {
    int server_fd;
    struct sockaddr_in server_addr, client_addr;
    socklen_t client_addr_len = sizeof(client_addr);
    int listen_duration = DEFAULT_LISTEN_DURATION;
    char server_ip[INET_ADDRSTRLEN] = DEFAULT_SERVER_IP;
    int server_port = DEFAULT_SERVER_PORT;
    int force_listen = 0;  // default is to exit on successful BIND
    struct timespec start_time;
    int file_received = 0;  // Flag to track if a file was received
    int terminate = 0;      // Flag to signal we should exit after a successful BIND

    // Parse optional arguments.
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            print_help();
            return 0;
        } else if (strcmp(argv[i], "--ip") == 0 && i + 1 < argc) {
            strncpy(server_ip, argv[i + 1], INET_ADDRSTRLEN);
            i++;
        } else if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
            server_port = atoi(argv[i + 1]);
            i++;
        } else if (strcmp(argv[i], "--listen-duration") == 0 && i + 1 < argc) {
            listen_duration = atoi(argv[i + 1]);
            if (listen_duration <= 0) {
                fprintf(stderr, "ERR\tInvalid listen duration\n");
                return 1;
            }
            i++;
        } else if (strcmp(argv[i], "--force-listen") == 0) {
            force_listen = 1;
        } else {
            fprintf(stderr, "ERR\tInvalid argument: %s\n", argv[i]);
            return 1;
        }
    }

    // Log startup info to stderr.
    fprintf(stderr, "INFO\tStarting server on %s:%d for %d seconds\n", server_ip, server_port, listen_duration);
    ensure_output_directory();

    // Create socket.
    if ((server_fd = socket(AF_INET, SOCK_STREAM, 0)) == -1) {
        perror("Socket creation failed");
        return 1;
    }

    // Allow immediate reuse of the address/port.
    int opt = 1;
    if (setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt)) < 0) {
        perror("setsockopt(SO_REUSEADDR) failed");
        close(server_fd);
        return 1;
    }

    // Set listening socket to non-blocking.
    int flags = fcntl(server_fd, F_GETFL, 0);
    if (flags == -1) {
        perror("fcntl(F_GETFL) failed");
        close(server_fd);
        return 1;
    }
    if (fcntl(server_fd, F_SETFL, flags | O_NONBLOCK) == -1) {
        perror("fcntl(F_SETFL, O_NONBLOCK) failed");
        close(server_fd);
        return 1;
    }

    // Bind.
    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = inet_addr(server_ip);
    server_addr.sin_port = htons(server_port);

    if (bind(server_fd, (struct sockaddr*)&server_addr, sizeof(server_addr)) == -1) {
        perror("Binding failed");
        close(server_fd);
        return 1;
    }

    // Listen.
    if (listen(server_fd, 5) == -1) {
        perror("Listening failed");
        close(server_fd);
        return 1;
    }

    // Use monotonic clock to track start time.
    clock_gettime(CLOCK_MONOTONIC, &start_time);

    // Main loop: accept new client connections until listen_duration expires or termination is requested.
    while (1) {
        double diff = elapsed_time_sec(&start_time);
        if (diff >= listen_duration) {
            fprintf(stderr, "INFO\tListen duration expired\n");
            break;
        }
        if (terminate) {
            fprintf(stderr, "INFO\tSuccessful BIND received; exiting as per default behavior\n");
            break;
        }

        int client_fd = accept(server_fd, (struct sockaddr*)&client_addr, &client_addr_len);
        if (client_fd == -1) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                usleep(100000); // sleep 0.1s
                continue;
            } else {
                perror("Accept failed");
                usleep(100000);
                continue;
            }
        }

        fprintf(stderr, "INFO\tClient connected\n");

        // Reset accepted socket to blocking mode.
        {
            int client_flags = fcntl(client_fd, F_GETFL, 0);
            if (client_flags != -1) {
                client_flags &= ~O_NONBLOCK;
                fcntl(client_fd, F_SETFL, client_flags);
            }
        }

        // Wrap the accepted socket with a FILE* stream for line-based I/O.
        FILE *client_file = fdopen(client_fd, "r+");
        if (!client_file) {
            perror("fdopen failed");
            close(client_fd);
            continue;
        }

        // Use getline() to allow for very long input lines (up to 2 MB or more)
        char *line = NULL;
        size_t linecap = 0;
        while (getline(&line, &linecap, client_file) != -1) {
            // Remove trailing newline if present.
            size_t len = strlen(line);
            if (len > 0 && line[len - 1] == '\n')
                line[len - 1] = '\0';

            // Parse the command and argument by splitting on the first whitespace (or tab).
            char *cmd = line;
            char *arg = NULL;
            char *sep = strpbrk(line, " \t");
            if (sep != NULL) {
                *sep = '\0';
                arg = sep + 1;
                while (*arg == ' ' || *arg == '\t') {
                    arg++;
                }
            }

            if (strcmp(cmd, "VERSION") == 0) {
                fprintf(client_file, "OK\tOpenIPC bind v0.1\n");
                fflush(client_file);
            } else if (strcmp(cmd, "BIND") == 0) {
                if (arg == NULL) {
                    fprintf(client_file, "ERR\tMissing argument for BIND command\n");
                    fflush(client_file);
                    continue;
                }
                file_received = 1;
                fprintf(stderr, "DEBUG: Received BIND command with base64 length: %zu\n", strlen(arg));
                if (base64_decode_and_save(arg, strlen(arg)) == 0) {
                    fprintf(client_file, "OK\n");
                } else {
                    fprintf(client_file, "ERR\tFailed to process data\n");
                }
                fflush(client_file);
                if (!force_listen) {
                    terminate = 1;
                    break;  // Break out of this connection's loop.
                }
            } else {
                fprintf(client_file, "ERR\tUnknown command\n");
                fflush(client_file);
            }
        }
        free(line);
        fclose(client_file);
        fprintf(stderr, "INFO\tClient disconnected\n");
    }

    close(server_fd);

    if (!file_received) {
        return 5;
    }

    return 0;
}

