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
#define DEFAULT_LISTEN_DURATION 10  // Default listen duration in seconds

void print_help() {
    printf("Usage: wfb_bind_rcv [OPTIONS]\n");
    printf("Options:\n");
    printf("  --ip <address>          Set server IP address (default: %s)\n", DEFAULT_SERVER_IP);
    printf("  --port <number>         Set server port (default: %d)\n", DEFAULT_SERVER_PORT);
    printf("  --listen-duration <sec> Set duration to listen before closing (default: %d seconds)\n", DEFAULT_LISTEN_DURATION);
    printf("  --help                  Show this help message\n");
}

/**
 * Ensures /tmp/bind directory exists
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

// Base64 decoding function
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
        if (c == '=' || c == '\n' || c == '\r') continue;
        char *pos = strchr("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", c);
        if (pos == NULL) continue;
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

static double elapsed_time_sec(const struct timespec *start)
{
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
    char buffer[BUFFER_SIZE];
    char command[BUFFER_SIZE], argument[BUFFER_SIZE];
    ssize_t received_bytes;
    int listen_duration = DEFAULT_LISTEN_DURATION;
    char server_ip[INET_ADDRSTRLEN] = DEFAULT_SERVER_IP;
    int server_port = DEFAULT_SERVER_PORT;
    struct timespec start_time;
    int file_received = 0;  // Flag to track if a file was received

    // Parse optional arguments
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
        } else {
            fprintf(stderr, "ERR\tInvalid argument: %s\n", argv[i]);
            return 1;
        }
    }

    printf("INFO\tStarting server on %s:%d for %d seconds\n", server_ip, server_port, listen_duration);
    ensure_output_directory();

    // Create socket
    if ((server_fd = socket(AF_INET, SOCK_STREAM, 0)) == -1) {
        perror("Socket creation failed");
        return 1;
    }

    // Set socket to non-blocking
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

    // Bind
    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = inet_addr(server_ip);
    server_addr.sin_port = htons(server_port);

    if (bind(server_fd, (struct sockaddr*)&server_addr, sizeof(server_addr)) == -1) {
        perror("Binding failed");
        close(server_fd);
        return 1;
    }

    // Listen
    if (listen(server_fd, 5) == -1) {
        perror("Listening failed");
        close(server_fd);
        return 1;
    }

    // Use monotonic clock to track start time
    clock_gettime(CLOCK_MONOTONIC, &start_time);

    // Main loop
    while (1) {
        double diff = elapsed_time_sec(&start_time);
        if (diff >= listen_duration) {
            printf("INFO\tListen duration expired\n");
            break;
        }

        // Try accepting a client
        int client_fd = accept(server_fd, (struct sockaddr*)&client_addr, &client_addr_len);
        if (client_fd == -1) {
            // If there's no incoming connection, sleep a bit and continue
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                usleep(100000); // sleep 0.1s
                continue;
            } else {
                perror("Accept failed");
                // We'll keep listening in case it was just a transient error
                usleep(100000);
                continue;
            }
        }

        printf("INFO\tClient connected\n");

        // Process client data
        while ((received_bytes = recv(client_fd, buffer, sizeof(buffer) - 1, 0)) > 0) {
            buffer[received_bytes] = '\0';

            // Attempt to parse command + argument
            // If only 1 token was read, 'argument' might be uninitialized
            // So be careful. Initialize them to empty strings each time.
            memset(command, 0, sizeof(command));
            memset(argument, 0, sizeof(argument));

            int tokens = sscanf(buffer, "%s %[^\t\n]", command, argument);

            if (tokens < 1) {
                fprintf(stderr, "ERR\tInvalid command format\n");
                send(client_fd, "ERR\tInvalid command format\n", 27, 0);
                break;  // Break from reading this client, but keep server running
            }

            if (strcmp(command, "VERSION") == 0) {
                send(client_fd, "OK\tOpenIPC bind v0.1\n", 21, 0);
            } 
            else if (strcmp(command, "BIND") == 0) {
                file_received = 1;  // Mark that a file was received
                if (base64_decode_and_save(argument, strlen(argument)) == 0) {
                    send(client_fd, "OK\n", 3, 0);
                } else {
                    send(client_fd, "ERR\tFailed to process data\n", 27, 0);
                }
            } 
            else {
                send(client_fd, "ERR\tUnknown command\n", 21, 0);
            }
        }

        close(client_fd);
        printf("INFO\tClient disconnected\n");
    }

    close(server_fd);

    // If timeout expired and no file was received, return exit code 5
    if (!file_received) {
        return 5;
    }

    return 0;
}

