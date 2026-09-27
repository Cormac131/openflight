/* Integer-only text helpers shared by the IWR6843 decision modules.
 *
 * The R4F CLI printf may lack %f, so every module prints floats through
 * l3_text_fixed2 (two decimals, rounded, sign kept). Pure C.
 */
#ifndef L3_TEXT_H
#define L3_TEXT_H

#include <stdint.h>

/* value with 0, 1 or 2 decimals ("12", "12.3", "12.34", "-0.50"), rounded;
 * magnitudes are clamped at 4e9 so the cast is defined. Fixed formats rather
 * than "%0*u": the R4F runtime's printf subset need not take a '*' width. */
void l3_text_fixed(float value, uint32_t decimals, char *out, uint32_t cap);
/* l3_text_fixed with two decimals. */
void l3_text_fixed2(float value, char *out, uint32_t cap);
/* Degrees from radians, two decimals. */
void l3_text_degrees2(float radians, char *out, uint32_t cap);

#endif /* L3_TEXT_H */
