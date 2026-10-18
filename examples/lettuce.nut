# Lettuce NFT recipe: 50 L reservoir, two-part base plus micronutrients.
# Product percentages come from your bag label; these are examples.

reservoir 50 L
uptake 2.5
ec_factor 640

target N 180
target Ca 160
target Mg 50
target S 65
target K 200
target P 40
target Fe 2

# Stock tanks: 100x concentrate, 2 L each
tank A 100x 2 L
tank B 100x 2 L
tank C 100x 1 L

# Products are solved in file order: each one feeds its element, then
# its other elements are counted before the next product is sized.
product CalNit A Ca=19 N=15.5 price=4.20/kg feeds=Ca
product MKP B K=28.7 P=22.7 price=5.40/kg feeds=P
product MgSO4 B Mg=9.8 S=13 price=1.25/kg feeds=Mg
product KNO3 A K=38.2 N=13 price=3.10/kg feeds=K
product ChelFe C Fe=6 price=12.00/kg feeds=Fe

stage seedling 0.5
stage vegetative 1.0
