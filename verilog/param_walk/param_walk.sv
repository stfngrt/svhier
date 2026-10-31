// One module instantiated with different parameters; each setting takes a different
// generate branch, so its children differ per instance.
module pw_deep; endmodule
module pw_leaf_a; endmodule
module pw_leaf_b; pw_deep u_deep(); endmodule

module pw_mid #(parameter int N = 0);
  if (N == 1) begin : g_a
    pw_leaf_a u_a();
  end else begin : g_b
    pw_leaf_b u_b();
  end
endmodule

module pw_top;
  pw_mid #(.N(1)) u_m1();
  pw_mid #(.N(2)) u_m2();
  pw_mid #(.N(1)) u_m3();
endmodule
