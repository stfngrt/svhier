// Uses an environment variable, +incdir+, +define+ and a nested -F.
+incdir+${SVHIER_CF_DIR}/inc
+define+CF_EN
-F ${SVHIER_CF_DIR}/inner.f
${SVHIER_CF_DIR}/rtl/cf_top.sv
${SVHIER_CF_DIR}/rtl/cf_tb.sv
