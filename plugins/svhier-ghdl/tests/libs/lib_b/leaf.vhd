entity leaf is end entity;
architecture b of leaf is
  component only_in_b is end component;
begin
  u_from_b : only_in_b;
end architecture;
