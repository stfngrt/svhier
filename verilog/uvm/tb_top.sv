// Top-level testbench: DUT + APB agent wired through the parameterised APB interface
module dut (apb_if.slave_mp apb);
endmodule

module tb_top;
  logic clk = 0;
  logic rst = 1;
  apb_if #(.ADDR_WIDTH(32), .DATA_WIDTH(32)) u_if (.pclk(clk), .presetn(rst));
  dut       u_dut   (.apb(u_if));
  apb_agent u_agent (.apb(u_if));
endmodule
