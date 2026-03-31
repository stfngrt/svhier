// Parameterized register module
module Reg
  #(parameter int WIDTH = 8)
  (input  logic             clk,
   input  logic [WIDTH-1:0] d,
   output logic [WIDTH-1:0] q);
  always_ff @(posedge clk) q <= d;
endmodule

// Bank of registers with different widths
module RegBank
  (input logic clk);
  Reg #(.WIDTH(8))  u_reg8  (.clk(clk), .d('0), .q());
  Reg #(.WIDTH(16)) u_reg16 (.clk(clk), .d('0), .q());
  Reg #(.WIDTH(32)) u_reg32 (.clk(clk), .d('0), .q());
endmodule
