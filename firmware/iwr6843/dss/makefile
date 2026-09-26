###################################################################################
# OpenFlight on-chip solve DSS firmware  (IWR6843, DSS / C674x only)
#
# Milestone 1 (Task 2): a minimal image that boots alongside the existing MSS
# image, proving the three-image meta-image build/link plumbing. Mirrors the
# structure of ../makefile (the MSS one), with R4F_* swapped for the C674x
# equivalents mmwave_sdk.mak already defines (C674_CFLAGS/C674_LDFLAGS etc.,
# fed the same MMWAVE_L3RAM_NUM_BANK / MMWAVE_SHMEM_BANK_SIZE --defines as the
# R4F side). Invoked out-of-tree the same way:
#
#   source $MMWAVE_SDK_INSTALL_PATH/../scripts/unix/setenv.sh   # device=iwr68xx
#   cd /work/firmware/iwr6843/dss
#   make            # -> l3_dump_dss.xe674  (CCS-loadable / debug)
#
# Normally invoked from ../makefile's `bin` target, not directly.
###################################################################################

include $(MMWAVE_SDK_INSTALL_PATH)/ti/common/mmwave_sdk.mak

.DEFAULT_GOAL := all

APPNAME = l3_dump_dss

# Sources live alongside this makefile.
vpath %.c .

###################################################################################
# Libraries. C674_COMMON_STD_LIB already provides osal+soc. Milestone 1 does
# not yet talk over the mailbox (Task_sleep only) or trip ESM, so neither of
# those libraries is linked yet -- Task 6 adds libmailbox_$(MMWAVE_SDK_DEVICE_TYPE)
# when dss_solveTask becomes a real mailbox listener.
###################################################################################
DSS_STD_LIBS = $(C674_COMMON_STD_LIB)
DSS_LOC_LIBS = $(C674_COMMON_LOC_LIB)

CFG        = dss.cfg
CONFIGPKG  = dss_configPkg_$(MMWAVE_SDK_DEVICE_TYPE)
MAP        = $(APPNAME).map
OUT        = $(APPNAME).$(C674_EXE_EXT)
APP_CMD    = dss_linker.cmd
SOURCES    = dss_main.c
DEPENDS    = $(addprefix $(PLATFORM_OBJDIR)/, $(SOURCES:.c=.$(C674_DEP_EXT)))
OBJECTS    = $(addprefix $(PLATFORM_OBJDIR)/, $(SOURCES:.c=.$(C674_OBJ_EXT)))

.PHONY: all clean cleanApp help appRTSC

# --- SYS/BIOS (RTSC) configuration -------------------------------------------
appRTSC:
	@echo 'Configuring RTSC packages...'
	$(XS) --xdcpath="$(XDCPATH)" xdc.tools.configuro $(C674_XSFLAGS) -o $(CONFIGPKG) $(CFG)
	@echo 'Finished configuring packages'

# --- link the DSS executable --------------------------------------------------
all: BUILD_CONFIGPKG=$(CONFIGPKG)
all: C674_CFLAGS += --cmd_file=$(BUILD_CONFIGPKG)/compiler.opt
all: buildDirectories appRTSC $(OBJECTS)
	$(C674_LD) $(C674_LDFLAGS) $(DSS_LOC_LIBS) $(DSS_STD_LIBS)              \
	-l$(CONFIGPKG)/linker.cmd --map_file=$(MAP) $(OBJECTS)                  \
	$(PLATFORM_C674X_LINK_CMD) $(APP_CMD) $(C674_LD_RTS_FLAGS) -o $(OUT)
	@echo '******************************************************************'
	@echo 'Built $(OUT)'
	@echo '******************************************************************'

clean:
	@$(DEL) $(OBJECTS) $(DEPENDS) $(MAP) $(OUT)
	@$(DEL) $(CONFIGPKG)
	@$(DEL) $(PLATFORM_OBJDIR)

# Same-geometry firmware iterations can reuse the generated RTSC/SYS/BIOS
# package. Variant switches must continue to use the full `clean` target.
cleanApp:
	@$(DEL) $(OBJECTS) $(DEPENDS) $(MAP) $(OUT)
	@$(DEL) $(PLATFORM_OBJDIR)

help:
	@echo '****************************************************************'
	@echo '* OpenFlight on-chip solve DSS firmware targets'
	@echo 'all      -> build the DSS executable (l3_dump_dss.xe674)'
	@echo 'clean    -> remove build artifacts'
	@echo 'cleanApp -> remove app objects but retain generated RTSC packages'
	@echo '****************************************************************'

-include $(DEPENDS)
