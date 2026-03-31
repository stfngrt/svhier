// Top-level module in a separate file, instantiating Sub from cross_file_sub.sv
module CrossTop;
  Sub      u_sub0 ();
  Sub      u_sub1 ();
  SubWithChild u_swc ();
endmodule
