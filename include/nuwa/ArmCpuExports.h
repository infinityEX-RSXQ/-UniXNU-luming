/* Declarations for the existing upstream ArmCpu C exports. No implementations. */
#pragma once

#include <IOKit/IOTypes.h>

#ifdef __cplusplus
extern "C" {
#endif

void armcpu_gic_diag(uint64_t aff, uint32_t *out);
void armcpu_sgi_selfprobe(uint64_t *out);
void armcpu_ipi_selftest(uint64_t aff);
IOReturn armcpu_spi_attach(uint32_t intid, bool edge, void (*handler)(void *), void *context,
                          uint32_t *readback, uint64_t *router);
uint64_t armcpu_spi_count(uint32_t intid);

#ifdef __cplusplus
}
#endif
