// Legacy Verilog-1995 style module
module Legacy(input clk, input rst, output reg q);
  always @(posedge clk or posedge rst)
    if (rst) q <= 1'b0;
    else     q <= ~q;
endmodule
