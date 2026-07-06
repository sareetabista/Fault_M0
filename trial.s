	.cpu cortex-m0
	.arch armv6-m
	.fpu softvfp
	.eabi_attribute 20, 1
	.eabi_attribute 21, 1
	.eabi_attribute 23, 3
	.eabi_attribute 24, 1
	.eabi_attribute 25, 1
	.eabi_attribute 26, 1
	.eabi_attribute 30, 2
	.eabi_attribute 34, 0
	.eabi_attribute 18, 4
	.file	"main.c"
	.text
	.align	1
	.p2align 2,,3
	.global	isw_and
	.syntax unified
	.code	16
	.thumb_func
	.type	isw_and, %function
isw_and:
	@ args = 0, pretend = 0, frame = 8
	@ frame_needed = 0, uses_anonymous_args = 0
	push	{r4, r5, r6, lr}         @5
	movs	r4, r2                    @ 1
	movs	r6, r0       @ 1
	movs	r5, r1  @ 1
	ldr	r2, [r0]   @2
	ldr	r3, [r1]  @ 2
	sub	sp, sp, #8  @ 1
	ands	r3, r2   @ 1                       // leaking      //sample 141-144             35,36
	str	r3, [r4]       @ 2                 //leaking       //sample 145-148,149-152    36.25, 37,38
	ldr	r2, [r0, #4]    @ 2    //41
	ldr	r3, [r1, #4]  @2
	add	r0, sp, #4    @ 1
	ands	r3, r2   @ 1
	str	r3, [r4, #4]  @ 2                   // 41
	bl	getRandomness        @ 4 +        //24+4+57+2 = 89
	nop 
	nop 
	nop
	ldr	r3, [r6]         @ 2
	ldr	r0, [r6, #4]    @ 2
	ldr	r1, [r5, #4]     @ 2
	ldr	r2, [r5]        @ 2                // leaking highest    sample
	ands	r1, r3     @ 1                     //leaking
	ands	r2, r0     @ 1                     /not exactly leaking
	ldr	r3, [sp, #4]   @ 2
	ldr	r0, [r4]      @ 2                   //second cycle leaking
	eors	r0, r3    @ 1                       // not exactly
	str	r0, [r4]    @ 2  83
	ldr	r0, [r4, #4]   @ 2    -- cl 87 
	eors	r3, r0     @ 1
	eors	r3, r1    @ 1
	eors	r3, r2    @ 1
	str	r3, [r4, #4]  @ 2
	add	sp, sp, #8   @ 1                              //leaking
	@ sp needed
	pop	{r4, r5, r6, pc}   @ 4                      //pop mostly 2 cycle leaking
	.size	isw_and, .-isw_and
	.ident	"GCC: (GNU Toolchain for the Arm Architecture 11.2-2022.02 (arm-11.14)) 11.2.1 20220111"
