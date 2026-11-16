/*
 * imessage-helper: the one program you give Full Disk Access.
 *
 * It does exactly one thing: start service.py (the read-only Messages helper) with a
 * fixed Python, in isolated mode with no site-packages. Before that it checks the
 * SHA-256 of every Python file the helper runs against hashes baked in when
 * install.sh compiled it. If any file was changed, it refuses to run, so no other
 * code can borrow its access. It takes no arguments and runs nothing else.
 *
 * Built by install.sh with -DPYTHON_PATH, -DPROJECT_DIR and -DHASH_* values.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef PYTHON_PATH
#error "build with install.sh"
#endif

/* ---- minimal SHA-256 (public domain style implementation) ---- */
typedef struct { uint32_t s[8]; uint64_t n; uint8_t b[64]; size_t bl; } sha;
static const uint32_t K[64] = {
0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
#define R(x,n) (((x)>>(n))|((x)<<(32-(n))))
static void blk(sha *c, const uint8_t *p) {
    uint32_t w[64], a,b,d,e,f,g,h,cc,t1,t2; int i;
    for (i=0;i<16;i++) w[i]=(uint32_t)p[4*i]<<24|(uint32_t)p[4*i+1]<<16|(uint32_t)p[4*i+2]<<8|p[4*i+3];
    for (;i<64;i++) w[i]=(R(w[i-2],17)^R(w[i-2],19)^(w[i-2]>>10))+w[i-7]+(R(w[i-15],7)^R(w[i-15],18)^(w[i-15]>>3))+w[i-16];
    a=c->s[0];b=c->s[1];cc=c->s[2];d=c->s[3];e=c->s[4];f=c->s[5];g=c->s[6];h=c->s[7];
    for (i=0;i<64;i++){t1=h+(R(e,6)^R(e,11)^R(e,25))+((e&f)^(~e&g))+K[i]+w[i];
        t2=(R(a,2)^R(a,13)^R(a,22))+((a&b)^(a&cc)^(b&cc));h=g;g=f;f=e;e=d+t1;d=cc;cc=b;b=a;a=t1+t2;}
    c->s[0]+=a;c->s[1]+=b;c->s[2]+=cc;c->s[3]+=d;c->s[4]+=e;c->s[5]+=f;c->s[6]+=g;c->s[7]+=h;
}
static void init(sha *c){static const uint32_t iv[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
    0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};memcpy(c->s,iv,32);c->n=0;c->bl=0;}
static void upd(sha *c,const uint8_t *p,size_t len){while(len--){c->b[c->bl++]=*p++;c->n+=8;
    if(c->bl==64){blk(c,c->b);c->bl=0;}}}
static void fin(sha *c,char hex[65]){uint64_t n=c->n;uint8_t one=0x80,z=0;int i;upd(c,&one,1);
    while(c->bl!=56)upd(c,&z,1);for(i=7;i>=0;i--){uint8_t x=(uint8_t)(n>>(8*i));upd(c,&x,1);}
    for(i=0;i<8;i++)sprintf(hex+8*i,"%08x",c->s[i]);hex[64]=0;}

static int file_sha256(const char *path, char hex[65]) {
    FILE *f = fopen(path, "rb"); if (!f) return -1;
    sha c; init(&c); uint8_t buf[8192]; size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) upd(&c, buf, n);
    fclose(f); fin(&c, hex); return 0;
}

static pid_t child = 0;
static void forward(int sig) { if (child > 0) kill(child, sig); }

static const char *FILES[][2] = {
    {PROJECT_DIR "/service.py", HASH_SERVICE},
    {PROJECT_DIR "/imessage_plans/__init__.py", HASH_INIT},
    {PROJECT_DIR "/imessage_plans/db.py", HASH_DB},
};

int main(int argc, char **argv) {
    (void)argv;
    if (argc != 1) { fprintf(stderr, "imessage-helper takes no arguments\n"); return 2; }
    for (size_t i = 0; i < sizeof FILES / sizeof FILES[0]; i++) {
        char hex[65];
        if (file_sha256(FILES[i][0], hex) != 0 || strcmp(hex, FILES[i][1]) != 0) {
            fprintf(stderr, "Refusing to start: %s is missing or was changed since install. "
                            "Re-run install.sh if you made that change yourself.\n", FILES[i][0]);
            return 3;
        }
    }
    char *const args[] = {PYTHON_PATH, "-I", "-S", PROJECT_DIR "/service.py", NULL};
    static char home[1100];
    const char *h = getenv("HOME");
    snprintf(home, sizeof home, "HOME=%s", h ? h : "");
    char *const env[] = {"PATH=/usr/bin:/bin", "LANG=en_US.UTF-8", home, NULL};
    /* Stay the parent: macOS grants Full Disk Access to the process that started the
     * helper, so this launcher (not Python) must remain the responsible process. */
    child = fork();
    if (child < 0) { perror("fork"); return 4; }
    if (child == 0) {
        execve(PYTHON_PATH, args, env);
        perror("execve");
        _exit(4);
    }
    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    int status = 0;
    while (waitpid(child, &status, 0) < 0) { /* retry if interrupted by a signal */ }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
