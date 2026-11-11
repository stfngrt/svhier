entity leaf is end entity;
architecture a of leaf is
  component only_in_a is end component;
begin
  u_from_a : only_in_a;
end architecture;
