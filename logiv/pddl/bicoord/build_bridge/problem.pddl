(define (problem build-bridge-initial)
  (:domain build-bridge)
  (:objects
    left right - arm
    blue-left blue-right - support
    red-beam - beam
  )
  (:init
    (empty left)
    (empty right)
    (left-arm left)
    (right-arm right)
    (support-arm left blue-left)
    (support-arm right blue-right)
    (spans red-beam blue-left blue-right)
    (support-on-table blue-left)
    (support-on-table blue-right)
    (beam-on-table red-beam)
  )
  (:goal (and
    (bridge-ready red-beam blue-left blue-right)
    (support-ready blue-left)
    (support-ready blue-right)
    (empty left)
    (empty right)
  ))
)
