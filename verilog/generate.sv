// Instances inside generate constructs (for / if / unnamed / nested).
module gen_other; endmodule

module gen_mid;
  if (1) begin : g_inner
    gen_leaf u_deep();
  end
endmodule

module gen_top #(parameter bit EN = 0);
  for (genvar i = 0; i < 2; i++) begin : g_loop
    gen_leaf u_loop();
  end

  if (EN) begin : g_on
    gen_other u_on();
  end else begin : g_off
    gen_leaf u_off();
  end

  if (1) gen_other u_unnamed();

  for (genvar j = 0; j < 1; j++) begin : g_outer
    if (1) begin : g_nest
      gen_mid u_mid();
    end
  end
endmodule
