-- `work` here means lib_b, the library this file belongs to.
entity chain is end entity;
architecture c of chain is begin
  u_helper : entity work.helper;
end architecture;
