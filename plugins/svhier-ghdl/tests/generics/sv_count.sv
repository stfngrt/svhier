module sv_count #(parameter int COUNT) ();
  for (genvar i = 0; i < COUNT; i++) begin : g
    sv_leaf u_leaf ();
  end
endmodule
