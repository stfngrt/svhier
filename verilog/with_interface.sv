// Interface for a simple data bus
interface BusIf (input logic clk);
  logic [7:0] data;
  logic       valid;

  modport out_mp (output data, valid, input clk);
  modport in_mp  (input  data, valid, clk);
endinterface

// Sender drives the bus
module Sender (BusIf.out_mp bus);
  always_ff @(posedge bus.clk)
    bus.data <= bus.data + 1;
endmodule

// Receiver reads the bus
module Receiver (BusIf.in_mp bus);
  logic [7:0] capture;
  always_ff @(posedge bus.clk)
    if (bus.valid) capture <= bus.data;
endmodule

// System wires Sender and Receiver through the interface
module System;
  logic clk;
  BusIf bus (.clk(clk));      // interface instance — should NOT appear in insts
  Sender   u_tx (.bus(bus));
  Receiver u_rx (.bus(bus));
endmodule
