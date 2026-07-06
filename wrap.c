#include "hal.h"
#include "simpleserial.h"
#include <stdint.h>
#include <string.h>

#define N_SHARES 2
#define RANDOM_WORDS 1

extern void locality_refresh(uint32_t in_x[N_SHARES],
                             uint32_t out_z[N_SHARES]);

static uint32_t randomness_pool[RANDOM_WORDS];
static uint32_t rand_idx = 0;

/* ===================================================== */
/* RANDOMNESS ACCESS                                     */
/* ===================================================== */

void getRandomness(uint32_t *r)
{
    if (rand_idx >= RANDOM_WORDS)
    {
        *r = 0;
        return;
    }

    *r = randomness_pool[rand_idx++];
}

/* ===================================================== */
/* LOAD RANDOMNESS                                       */
/* ===================================================== */

uint8_t load_randomness(uint8_t *data, uint8_t len)
{
    if (len != 4)
        return 0x01;

    memcpy(randomness_pool, data, 4);

    rand_idx = 0;

    return 0x00;
}

/* ===================================================== */
/* MAIN CRYPTO FUNCTION                                  */
/* ===================================================== */

uint8_t run(uint8_t *pt, uint8_t len)
{
    if (len != 8)
        return 0x01;

    uint32_t share0;
    uint32_t share1;

    memcpy(&share0, &pt[0], 4);
    memcpy(&share1, &pt[4], 4);

    uint32_t in[N_SHARES];
    uint32_t out[N_SHARES];

    in[0] = share0;
    in[1] = share1;

    rand_idx = 0;

    /* ------------------------------------------------- */
    /* PRE-TRIGGER STABILIZATION                         */
    /* ------------------------------------------------- */

    for (volatile int i = 0; i < 50; i++)
    {
        __asm__("nop");
    }

    /* ------------------------------------------------- */
    /* TRIGGER START                                     */
    /* ------------------------------------------------- */

    trigger_high();

    locality_refresh(in, out);

    trigger_low();

    /* ------------------------------------------------- */
    /* UART OUTSIDE TRACE WINDOW                         */
    /* ------------------------------------------------- */

    simpleserial_put('r', 8, (uint8_t*)out);

    return 0x00;
}

/* ===================================================== */
/* MAIN                                                  */
/* ===================================================== */

int main(void)
{
    platform_init();

    init_uart();

    trigger_setup();

    simpleserial_init();

    simpleserial_addcmd('p', 4, load_randomness);

    simpleserial_addcmd('r', 8, run);

    while (1)
    {
        simpleserial_get();
    }
}