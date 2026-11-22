// Synthetic RomFS index for the production lookup and directory-listing code cut out by test_romfs_list.py.
// Usage: t_romfs_list PATH STOP REPEAT < rows.tsv; each row is: added(0/1) size overlay-size(-1=none) path.
// Emits directory entries, the result/type codes, and index reads (a deterministic bound on subtree traversal).
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fs.h"
#include "romfs_path.h"
#include "list.inc"

static int emitted, stop_after, quiet;
static int listed(const char *name, int is_dir, uint64_t size, void *ctx) {
    (void)ctx;
    if (!quiet) printf("%d\t%" PRIu64 "\t%s\n", is_dir, size, name);
    emitted++;
    return stop_after && emitted >= abs(stop_after) ? (stop_after < 0 ? -1 : 1) : 0;
}

int main(int argc, char **argv) {
    if (argc != 4) return 2;
    stop_after = atoi(argv[2]);
    int repeat = atoi(argv[3]);
    size_t cap = 0, names = 0, pool_cap = 0;
    Entry *rows = NULL;
    char *pool = NULL;
    char line[8192], name[4096];
    int is_added;
    uint64_t size;
    int64_t replacement;
    while (fgets(line, sizeof line, stdin)) {
        if (sscanf(line, "%d\t%" SCNu64 "\t%" SCNd64 "\t%4095[^\n]", &is_added, &size, &replacement, name) != 4) return 3;
        if (file_count + nadded == cap) {
            cap = cap ? cap * 2 : 16;
            rows = realloc(rows, cap * sizeof *rows);
            added = realloc(added, cap * sizeof *added);
            overlays = realloc(overlays, cap * sizeof *overlays);
        }
        uint64_t index;
        if (is_added) {
            added[nadded] = malloc(strlen(name) + 1);
            strcpy(added[nadded], name);
            index = file_count + nadded++;
        } else {
            size_t len = strlen(name) + 1;
            if (names + len > pool_cap) {
                pool_cap = pool_cap ? pool_cap * 2 : 4096;
                if (pool_cap < names + len) pool_cap = names + len;
                pool = realloc(pool, pool_cap);
            }
            memcpy(pool + names, name, len);
            rows[file_count] = (Entry){0, size, (uint32_t)names, (uint32_t)(len - 1)};
            names += len;
            index = file_count++;
        }
        if (replacement >= 0 || is_added)
            overlays[noverlay++] = (Overlay){.index = index, .size = replacement >= 0 ? (uint64_t)replacement : size};
    }
    strings_offset = 24 + file_count * sizeof *rows;
    index_bytes = calloc(1, (size_t)strings_offset + names);
    if (file_count) memcpy(index_bytes + 24, rows, file_count * sizeof *rows);
    if (names) memcpy(index_bytes + strings_offset, pool, names);
    free(rows);
    free(pool);
    uint32_t result = 0;
    for (int i = 0; i < repeat; i++) {
        emitted = 0;
        quiet = i > 0;
        result = romfs_list(argv[1], listed, NULL);
    }
    int type = -1;
    uint32_t typed = romfs_entry_type(&type, argv[1]);
    fprintf(stderr, "%u %u %d %" PRIu64 "\n", result, typed, type, entry_reads);
    free(index_bytes);
    for (size_t i = 0; i < nadded; i++) free(added[i]);
    free(added);
    free(overlays);
    return 0;
}
