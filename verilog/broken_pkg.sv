// Intentionally broken: imports a package that does not exist
module NoPkg
  import NonExistentPkg::*;
  (input logic a);
endmodule
