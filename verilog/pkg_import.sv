// Package defining shared types and constants
package MathPkg;
  parameter int WIDTH = 8;
  typedef logic [WIDTH-1:0] data_t;
endpackage

// Module importing the package and using its types
module Adder
  import MathPkg::*;
  #(parameter int W = WIDTH)
  (input data_t a, b, output data_t sum);
  assign sum = a + b;
endmodule

// Module with two parameterized Adder instances
module ALU
  import MathPkg::*;
  (input data_t x, y, z, output data_t result);
  data_t tmp;
  Adder #(.W(WIDTH)) u_add0 (.a(x), .b(y), .sum(tmp));
  Adder #(.W(WIDTH)) u_add1 (.a(tmp), .b(z), .sum(result));
endmodule
