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
#include <stdarg.h>
#include <sys/wait.h>

#define DEFAULT_SERVER_IP "10.5.99.2"
#define DEFAULT_SERVER_PORT 5555
#define BUFFER_SIZE 8192
#define OUTPUT_DIR "/tmp/bind"
#define OUTPUT_FILE "/tmp/bind/bind.tar.gz"
#define DEFAULT_LISTEN_DURATION 60  // seconds

// Exit code definitions.
#define EXIT_ERR    1
#define EXIT_BIND   2
#define EXIT_UNBIND 3

// Global flag for debug output.
static int debug_enabled = 0;

/*--------------------------------------------------
 * Helper Functions
 *--------------------------------------------------*/

// Print debug messages if debug is enabled.
void debug_print(const char *fmt, ...) {
    if (!debug_enabled)
        return;
    va_list args;
    va_start(args, fmt);
    fprintf(stderr, "DEBUG: ");
    vfprintf(stderr, fmt, args);
    va_end(args);
}

// Print usage help.
void print_help() {
    fprintf(stderr, "Usage: wfb_bind_rcv [OPTIONS]\n");
    fprintf(stderr, "Options:\n");
    fprintf(stderr, "  --ip <address>          Set server IP address (default: %s)\n", DEFAULT_SERVER_IP);
    fprintf(stderr, "  --port <number>         Set server port (default: %d)\n", DEFAULT_SERVER_PORT);
    fprintf(stderr, "  --listen-duration <sec> Set duration to listen before closing (default: %d seconds)\n", DEFAULT_LISTEN_DURATION);
    fprintf(stderr, "  --force-listen          Continue listening even after a terminating command\n");
    fprintf(stderr, "  --debug                 Enable debug output\n");
    fprintf(stderr, "  --help                  Show this help message\n");
}

// Ensure that the output directory exists.
void ensure_output_directory() {
    struct stat st = {0};
    if (stat(OUTPUT_DIR, &st) == -1) {
        if (mkdir(OUTPUT_DIR, 0777) != 0) {
            perror("Failed to create output directory");
            exit(EXIT_ERR);
        }
    }
}

// Base64 decode the input string and write the decoded data to OUTPUT_FILE.
// Returns 0 on success, nonzero on error.
int base64_decode_and_save(const char *input, size_t input_length) {
    FILE *output_file = fopen(OUTPUT_FILE, "wb");
    if (!output_file) {
        fprintf(stderr, "ERR\tFailed to open output file\n");
        return 1;
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

// Return elapsed time (in seconds) since 'start'.
static double elapsed_time_sec(const struct timespec *start) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    double seconds = (double)(now.tv_sec - start->tv_sec);
    double nsecs   = (double)(now.tv_nsec - start->tv_nsec) / 1e9;
    return seconds + nsecs;
}

// Execute a system command and capture its output in a dynamically allocated string.
// The caller is responsible for freeing the returned string.
char *execute_command(const char *cmd) {
    FILE *fp = popen(cmd, "r");
    if (!fp)
        return NULL;
    
    size_t size = 4096;
    char *output = malloc(size);
    if (!output) {
        pclose(fp);
        return NULL;
    }
    output[0] = '\0';
    size_t len = 0;
    char buffer[1024];
    while (fgets(buffer, sizeof(buffer), fp)) {
        size_t buffer_len = strlen(buffer);
        if (len + buffer_len + 1 > size) {
            size = (len + buffer_len + 1) * 2;
            char *temp = realloc(output, size);
            if (!temp) {
                free(output);
                pclose(fp);
                return NULL;
            }
            output = temp;
        }
        strcpy(output + len, buffer);
        len += buffer_len;
    }
    pclose(fp);
    return output;
}

/*
 * Remove newline characters from input by replacing them with a space.
 * Returns a newly allocated string which the caller must free.
 */
char *remove_newlines(const char *input) {
    size_t len = strlen(input);
    char *output = malloc(len + 1);
    if (!output)
        return NULL;
    for (size_t i = 0; i < len; i++) {
        if (input[i] == '\n' || input[i] == '\r')
            output[i] = ' ';
        else
            output[i] = input[i];
    }
    output[len] = '\0';
    return output;
}

/*--------------------------------------------------
 * Command Handler Declarations
 *--------------------------------------------------*/

typedef int (*command_handler)(const char *arg, FILE *client_file, int force_listen);

/*
 * Each command handler sends a reply to the connected peer.
 * If the command should cause the program to terminate (and force_listen is false),
 * the handler returns the exit code to use (nonzero). Otherwise, it returns 0.
 */

// VERSION: reply with version info.
int cmd_version(const char *arg, FILE *client_file, int force_listen) {
    (void)arg; // unused
    (void)force_listen;
    fprintf(client_file, "OK\tOpenIPC bind v0.1\n");
    fflush(client_file);
    return 0;
}

// BIND: decode base64 input and save to file.
int cmd_bind(const char *arg, FILE *client_file, int force_listen) {
    if (arg == NULL || strlen(arg) == 0) {
        fprintf(client_file, "ERR\tMissing argument for BIND command\n");
        fflush(client_file);
        return 0;
    }
    debug_print("Received BIND command with base64 length: %zu\n", strlen(arg));
    if (base64_decode_and_save(arg, strlen(arg)) == 0) {
        fprintf(client_file, "OK\n");
        fflush(client_file);
        if (!force_listen)
            return EXIT_BIND;
    } else {
        fprintf(client_file, "ERR\tFailed to process data\n");
        fflush(client_file);
    }
    return 0;
}

// UNBIND: execute the system command "firstboot".
int cmd_unbind(const char *arg, FILE *client_file, int force_listen) {
    (void)arg; // no argument needed
    debug_print("Received UNBIND command\n");
    int ret = system("firstboot");
    if (ret == -1) {
        fprintf(client_file, "ERR\tFailed to execute UNBIND command\n");
    } else if (WIFEXITED(ret) && WEXITSTATUS(ret) == 0) {
        fprintf(client_file, "OK\tUNBIND executed successfully\n");
        fflush(client_file);
        if (!force_listen)
            return EXIT_UNBIND;
    } else {
        fprintf(client_file, "ERR\tUNBIND command returned error code %d\n", WEXITSTATUS(ret));
    }
    fflush(client_file);
    return 0;
}

// INFO: execute "ipcinfo -cfvlFtixSV" and "lsusb", concatenate their output and send back.
// To keep the reply in a single line (like VERSION), we replace newline characters with spaces.
int cmd_info(const char *arg, FILE *client_file, int force_listen) {
    (void)arg; // no argument needed
    debug_print("Received INFO command\n");
    
    char *ipcinfo_out = execute_command("ipcinfo -cfvlFtixSV");
    char *lsusb_out = execute_command("lsusb");

    if (!ipcinfo_out) {
        ipcinfo_out = strdup("Failed to execute ipcinfo command");
    }
    if (!lsusb_out) {
        lsusb_out = strdup("Failed to execute lsusb command");
    }
    
    // Remove newline characters so the reply is a single line.
    char *ipcinfo_clean = remove_newlines(ipcinfo_out);
    char *lsusb_clean = remove_newlines(lsusb_out);
    
    size_t resp_size = strlen(ipcinfo_clean) + strlen(lsusb_clean) + 64;
    char *response = malloc(resp_size);
    if (response) {
        snprintf(response, resp_size, "%s | %s", ipcinfo_clean, lsusb_clean);
        fprintf(client_file, "OK\t%s\n", response);
        free(response);
    } else {
        fprintf(client_file, "ERR\tMemory allocation error\n");
    }
    free(ipcinfo_clean);
    free(lsusb_clean);
    free(ipcinfo_out);
    free(lsusb_out);
    fflush(client_file);
    return 0;
}

/*--------------------------------------------------
 * Command Dispatch
 *--------------------------------------------------*/

typedef struct {
    const char *name;
    command_handler handler;
} command_entry;

command_entry commands[] = {
    { "VERSION", cmd_version },
    { "BIND",    cmd_bind    },
    { "UNBIND",  cmd_unbind  },
    { "INFO",    cmd_info    },
    { NULL,      NULL        }  // Sentinel
};

/*
 * Dispatch a command based on the command lookup table.
 * Returns a nonzero exit code if the command requests termination; otherwise returns 0.
 */
int handle_command(const char *cmd, const char *arg, FILE *client_file, int force_listen) {
    for (int i = 0; commands[i].name != NULL; i++) {
        if (strcmp(cmd, commands[i].name) == 0) {
            return commands[i].handler(arg, client_file, force_listen);
        }
    }
    fprintf(client_file, "ERR\tUnknown command\n");
    fflush(client_file);
    return 0;
}

/*--------------------------------------------------
 * Main
 *--------------------------------------------------*/

int main(int argc, char *argv[]) {
    int server_fd;
    struct sockaddr_in server_addr, client_addr;
    socklen_t client_addr_len = sizeof(client_addr);
    int listen_duration = DEFAULT_LISTEN_DURATION;
    char server_ip[INET_ADDRSTRLEN] = DEFAULT_SERVER_IP;
    int server_port = DEFAULT_SERVER_PORT;
    int force_listen = 0;  // Default: terminate on a successful BIND/UNBIND.
    
    // exit_code will be set if a command requests termination.
    int exit_code = 0;   
    int command_terminated = 0;

    // Parse command-line arguments.
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
                exit(EXIT_ERR);
            }
            i++;
        } else if (strcmp(argv[i], "--force-listen") == 0) {
            force_listen = 1;
        } else if (strcmp(argv[i], "--debug") == 0) {
            debug_enabled = 1;
        } else {
            fprintf(stderr, "ERR\tInvalid argument: %s\n", argv[i]);
            exit(EXIT_ERR);
        }
    }

    fprintf(stderr, "INFO\tStarting server on %s:%d for %d seconds\n", server_ip, server_port, listen_duration);
    ensure_output_directory();

    // Create socket.
    if ((server_fd = socket(AF_INET, SOCK_STREAM, 0)) == -1) {
        perror("Socket creation failed");
        exit(EXIT_ERR);
    }

    // Allow immediate reuse of the address/port.
    int opt = 1;
    if (setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt)) < 0) {
        perror("setsockopt(SO_REUSEADDR) failed");
        close(server_fd);
        exit(EXIT_ERR);
    }

    // Set the listening socket to non-blocking.
    int flags = fcntl(server_fd, F_GETFL, 0);
    if (flags == -1) {
        perror("fcntl(F_GETFL) failed");
        close(server_fd);
        exit(EXIT_ERR);
    }
    if (fcntl(server_fd, F_SETFL, flags | O_NONBLOCK) == -1) {
        perror("fcntl(F_SETFL, O_NONBLOCK) failed");
        close(server_fd);
        exit(EXIT_ERR);
    }

    // Bind.
    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = inet_addr(server_ip);
    server_addr.sin_port = htons(server_port);
    if (bind(server_fd, (struct sockaddr*)&server_addr, sizeof(server_addr)) == -1) {
        perror("Binding failed");
        close(server_fd);
        exit(EXIT_ERR);
    }

    // Listen.
    if (listen(server_fd, 5) == -1) {
        perror("Listening failed");
        close(server_fd);
        exit(EXIT_ERR);
    }

    // Start the timer.
    struct timespec start_time;
    clock_gettime(CLOCK_MONOTONIC, &start_time);

    // Main loop: accept clients until listen_duration expires or a command terminates the server.
    while (1) {
        double diff = elapsed_time_sec(&start_time);
        if (diff >= listen_duration) {
            fprintf(stderr, "INFO\tListen duration expired\n");
            break;
        }
        if (command_terminated) {
            fprintf(stderr, "INFO\tA command requested termination\n");
            break;
        }

        int client_fd = accept(server_fd, (struct sockaddr*)&client_addr, &client_addr_len);
        if (client_fd == -1) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                usleep(100000); // 0.1 second
                continue;
            } else {
                perror("Accept failed");
                usleep(100000);
                continue;
            }
        }
        fprintf(stderr, "INFO\tClient connected\n");

        // Set accepted socket to blocking mode.
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

        char *line = NULL;
        size_t linecap = 0;
        // Process commands from this client.
        while (getline(&line, &linecap, client_file) != -1) {
            // Remove trailing newline.
            size_t len = strlen(line);
            if (len > 0 && line[len - 1] == '\n')
                line[len - 1] = '\0';

            // Split the input into a command and an optional argument.
            char *cmd = line;
            char *arg = NULL;
            char *sep = strpbrk(line, " \t");
            if (sep != NULL) {
                *sep = '\0';
                arg = sep + 1;
                // Skip additional whitespace.
                while (*arg == ' ' || *arg == '\t')
                    arg++;
                if (*arg == '\0')
                    arg = NULL;
            }

            // Dispatch the command.
            int ret = handle_command(cmd, arg, client_file, force_listen);
            if (ret != 0) {
                exit_code = ret;
                command_terminated = 1;
                break;
            }
        }
        free(line);
        fclose(client_file);
        fprintf(stderr, "INFO\tClient disconnected\n");

        if (command_terminated)
            break;
    }

    close(server_fd);

    // If no command requested termination (listen timeout), exit with 0.
    exit(exit_code);
}

