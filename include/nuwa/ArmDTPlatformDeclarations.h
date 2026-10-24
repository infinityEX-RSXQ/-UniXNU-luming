// The upstream platformStageReset executes PSCI SYSTEM_RESET and then remains
// in an unconditional WFE loop. Declare that existing non-returning contract;
// do not change its implementation or suppress -Wmissing-noreturn.
#pragma once

[[noreturn]] static void platformStageReset(void);
