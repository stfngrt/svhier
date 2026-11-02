library lib_b;
library utils;
use utils.util_pkg.all;
entity lib_top is end entity;
architecture rtl of lib_top is
begin
  u_leaf : entity lib_b.leaf;
  u_chain : entity lib_b.chain;
end architecture;
