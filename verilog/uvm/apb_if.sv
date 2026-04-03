// Parameterised APB interface with modports used by the UVM agent and DUT
interface apb_if #(
  parameter int ADDR_WIDTH = 32,
  parameter int DATA_WIDTH = 32
)(input logic pclk, presetn);

  logic [ADDR_WIDTH-1:0] paddr;
  logic [DATA_WIDTH-1:0] pwdata, prdata;
  logic                  psel, penable, pwrite, pready, pslverr;

  modport master_mp(output paddr, pwdata, psel, penable, pwrite,
                    input  prdata, pready, pslverr, pclk, presetn);
  modport slave_mp (input  paddr, pwdata, psel, penable, pwrite, pclk, presetn,
                    output prdata, pready, pslverr);
endinterface
