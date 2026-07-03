CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra
BUILD_DIR := build

.PHONY: all clean

all: $(BUILD_DIR)/analise_xml_nfe $(BUILD_DIR)/analise_log_nfse $(BUILD_DIR)/autoexec_automation $(BUILD_DIR)/analise_xml_nfce

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/analise_xml_nfe: Analise_xml/src/main.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) $< -o $@

$(BUILD_DIR)/analise_log_nfse: Analise_LogNFSE/src/main.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) $< -o $@

$(BUILD_DIR)/autoexec_automation: Autoexec_automation/src/generator.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) $< -o $@

$(BUILD_DIR)/analise_xml_nfce: analise_xml_nfce/src/main.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) $< -o $@

clean:
	rm -rf $(BUILD_DIR)
