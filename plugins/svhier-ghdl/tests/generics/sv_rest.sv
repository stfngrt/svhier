// The default is 0, so seeing u_on proves the value VHDL gives its component reached slang.
module sv_gated #(parameter int EN = 0) ();
  if (EN) begin : on sv_leaf u_on (); end else begin : off sv_leaf u_off (); end
endmodule
module sv_case #(parameter int DEPTH) ();
  for (genvar i = 0; i < DEPTH; i++) begin : d sv_leaf u_d (); end
endmodule
module sv_setting #(parameter int MODE) ();
  if (MODE == 0) begin : m0 sv_leaf u_mode0 (); end else begin : m1 sv_leaf u_mode1 (); end
endmodule
module sv_kinds #(parameter int I = 0, parameter int NEG = 0, parameter bit B = 0, parameter logic SL = 0,
                  parameter logic [3:0] V = 0, parameter string S = "", parameter real R = 0.0) ();
  if (I == 6) begin : a sv_leaf u_i (); end
  if (NEG == -4) begin : b sv_leaf u_neg (); end
  if (B) begin : c sv_leaf u_b (); end
  if (SL) begin : d sv_leaf u_sl (); end
  if (V == 4'b1010) begin : e sv_leaf u_v (); end
  if (S == "hi there") begin : f sv_leaf u_s (); end
  if (R > 2.0) begin : h sv_leaf u_r (); end
endmodule
module sv_time #(parameter int W = 0) ();
  if (W == 2) begin : w sv_leaf u_w (); end
endmodule
module sv_needs_time #(parameter int T) (); sv_leaf u_never (); endmodule
module sv_extra; sv_leaf u_x (); endmodule
