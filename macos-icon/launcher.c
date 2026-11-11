/* Native launcher for VoiceToText.app. Embeds the venv's Python (libpython loaded at runtime) so the
 * running process IS the app bundle's executable; macOS then lists "VoiceToText" (not "python") under
 * Privacy & Security. Built by install-macos.sh with -DPYLIB, -DPYBIN, -DAPPPY. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    void *lib = dlopen(PYLIB, RTLD_NOW | RTLD_GLOBAL);
    if (!lib) { fprintf(stderr, "cannot load %s: %s\n", PYLIB, dlerror()); return 1; }
    int (*run)(int, char **) = (int (*)(int, char **))dlsym(lib, "Py_BytesMain");
    if (!run) { fprintf(stderr, "Py_BytesMain not found\n"); return 1; }
    char *args[] = {PYBIN, APPPY, "daemon", NULL};  /* argv[0] = venv python so pyvenv.cfg is honoured */
    return run(3, args);
}
