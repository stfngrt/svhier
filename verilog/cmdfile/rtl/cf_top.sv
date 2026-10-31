`include "cf_defs.svh"
module cf_top;
`ifdef CF_EN
  cf_leaf u_leaf();
`endif
  logic [`CF_W-1:0] data;
endmodule
